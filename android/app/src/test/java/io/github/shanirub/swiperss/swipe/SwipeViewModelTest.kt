package io.github.shanirub.swiperss.swipe

import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.ViewModelStore
import androidx.lifecycle.viewModelScope
import androidx.lifecycle.viewmodel.initializer
import androidx.lifecycle.viewmodel.viewModelFactory
import io.github.shanirub.swiperss.api.Action
import io.github.shanirub.swiperss.api.ApiClient
import io.github.shanirub.swiperss.api.Swipe
import io.github.shanirub.swiperss.api.apiJson
import io.github.shanirub.swiperss.db.inMemoryDatabase
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.job
import kotlinx.coroutines.joinAll
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import mockwebserver3.MockResponse
import mockwebserver3.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Before
import org.junit.Test

// The view model's wiring: refill errors become a message, swipes get the card timer's time, and
// swipes are sent when the app comes back and shortly after swiping.
// viewModelScope runs on Android's main thread, which the JVM lacks; the test dispatcher stands in.
// Its delays run on virtual time, so the 5-second wait before sending passes at once.
@OptIn(ExperimentalCoroutinesApi::class)
class SwipeViewModelTest {
    private val fake = FakeServer()
    private val server = MockWebServer().apply { dispatcher = fake; start() }
    private val db = inMemoryDatabase()
    private val repository = SwipeRepository(ApiClient.create(server.url("/").toString(), TEST_TOKEN), db, appVersion = 1)
    private val store = ViewModelStore()

    @Volatile
    private var now = 0L

    @Before
    fun setMain() = Dispatchers.setMain(UnconfinedTestDispatcher())

    @After
    fun tearDown() {
        // Cancel the view model's coroutines and wait until they have ended: one still finishing
        // (Room answering on another thread) resumes on the main dispatcher, which must still be
        // the test's then. Otherwise a later test fails at random.
        val jobs = viewModels.map { it.viewModelScope.coroutineContext.job }
        store.clear()
        runBlocking { withTimeout(5_000) { jobs.joinAll() } }
        Dispatchers.resetMain()
        db.close()
        server.close()
    }

    private val viewModels = mutableListOf<SwipeViewModel>()

    private fun viewModel(): SwipeViewModel =
        ViewModelProvider.create(
            store,
            viewModelFactory { initializer { SwipeViewModel(repository, monotonicMs = { now }) } },
        )[SwipeViewModel::class].also { viewModels.add(it) }

    private suspend fun pendingSwipes(): List<Swipe> =
        db.outbox().pending(10).map { apiJson.decodeFromString<Swipe>(it.swipeJson) }

    @Test
    fun aFailedRefillShowsWhyUntilOneSucceeds() = runTest {
        val viewModel = viewModel()
        fake.queueAnswers.add(MockResponse.Builder().code(503).build())
        viewModel.onForeground()
        assertEquals("Server error 503", viewModel.state.first { !it.loading && it.problem != null }.problem)

        fake.queueAnswers.add(queueResponse(card("a")))
        viewModel.retry()
        // Room reports new cards a moment after the refill ends, so wait for the cards themselves.
        val state = viewModel.state.first { it.cards == listOf(card("a")) }
        assertEquals(null, state.problem)
    }

    @Test
    fun noConnectionIsShownAsOffline() = runTest {
        val viewModel = viewModel()
        server.close()
        viewModel.onForeground()
        assertEquals("Offline: can't reach the server (is Tailscale on?)", viewModel.state.first { it.problem != null }.problem)
    }

    @Test
    fun swipesCarryHowLongTheirCardWasShown() = runTest {
        fake.swipesAnswer = { jsonResponse(503, "{}") }  // keep the swipes on the phone to look at them
        val viewModel = viewModel()
        now = 1000
        fake.queueAnswers.add(queueResponse(card("a"), card("b")))
        viewModel.onForeground()
        viewModel.state.first { it.cards == listOf(card("a"), card("b")) }

        now = 3500
        viewModel.swipe(card("a"), Action.SAVE)
        viewModel.state.first { it.cards == listOf(card("b")) }
        // b became the top card at 3500, then the app was in the background until 9000: that doesn't count.
        now = 9000
        viewModel.onForeground()
        now = 9800
        viewModel.swipe(card("b"), Action.NEVER)
        viewModel.state.first { it.cards == emptyList<Any>() }

        val times = pendingSwipes().associate { it.card.itemKey to it.timeToSwipeMs }
        assertEquals(mapOf("a" to 2500L, "b" to 800L), times)
    }

    @Test
    fun swipesAreSentShortlyAfterSwiping() = runTest {
        db.cards().addCards(listOf(card("a"), card("b")))
        val viewModel = viewModel()
        viewModel.state.first { it.cards?.size == 2 }

        viewModel.swipe(card("a"), Action.SAVE)
        viewModel.swipe(card("b"), Action.NEVER)

        eventually("both swipes sent") { fake.swipeBatches.flatten().size == 2 }
        eventually("outbox empty") { db.outbox().pending(10).isEmpty() }
    }

    @Test
    fun cardsAreAskedForAgainAfterSwipesAreSent() = runTest {
        // While the connection was down, 49 swipes piled up; one card is left. The server has 60
        // cards and serves the oldest 50 it has no swipe for.
        val cards = (0 until 60).map { card("c$it") }
        fake.items = cards
        db.cards().addCards(listOf(cards[49]))
        cards.take(49).forEach { repository.swipe(it, Action.NEVER, null) }
        val viewModel = viewModel()
        viewModel.state.first { it.cards?.size == 1 }

        viewModel.swipe(cards[49], Action.NEVER)
        // Block (don't suspend) until that refill's request is out: the test scheduler can't run
        // the delayed sync meanwhile, so the refill goes first, as on the phone. It gets c0 to c49,
        // which the phone all has: the stack stays empty.
        repeat(500) { if (fake.arrivals.isEmpty()) Thread.sleep(10) }

        eventually("the next 10 cards") { db.cards().observeCards().first() == cards.drop(50) }
        assertEquals(listOf("/queue", "/swipes", "/queue"), fake.arrivals.map { it.first })
    }

    @Test
    fun comingBackSendsSwipesBeforeAskingForCards() = runTest {
        db.cards().addCards(listOf(card("a")))
        repository.swipe(card("a"), Action.SAVE, null)
        val viewModel = viewModel()

        viewModel.onForeground()

        eventually("cards asked for") { fake.arrivals.any { it.first == "/queue" } }
        assertEquals(listOf("/swipes", "/queue"), fake.arrivals.map { it.first })
        assertEquals(emptyList<Swipe>(), pendingSwipes())
    }

    @Test
    fun unsentSwipesAreShownWhileSendingFails() = runTest {
        db.cards().addCards(listOf(card("a")))
        repository.swipe(card("a"), Action.SAVE, null)
        fake.swipesAnswer = { jsonResponse(503, "{}") }
        val viewModel = viewModel()
        // Pending alone is normal (the swipe is about to go): nothing to report yet.
        assertNull(viewModel.state.first { it.cards != null }.unsent)

        viewModel.onForeground()
        assertEquals(1, viewModel.state.first { it.unsent != null }.unsent)

        fake.swipesAnswer = { swipesStored(it.size) }
        viewModel.onForeground()
        eventually("outbox empty") { db.outbox().pending(10).isEmpty() }
        assertNull(viewModel.state.first { it.unsent == null }.unsent)
    }

    @Test
    fun deadLettersAreShownAndCanBeRetried() = runTest {
        db.cards().addCards(listOf(card("a")))
        repository.swipe(card("a"), Action.SAVE, null)
        val id = db.outbox().pending(1).single().swipeId
        db.outbox().markDead(id, "HTTP 422: bad", appVersion = 1)  // this version: no automatic retry
        val viewModel = viewModel()
        viewModel.onForeground()
        assertEquals(1, viewModel.state.first { it.dead == 1 }.dead)
        eventually("cards asked for") { fake.arrivals.any { it.first == "/queue" } }
        assertEquals(emptyList<List<String>>(), fake.swipeBatches)

        viewModel.retryDeadLetters()

        viewModel.state.first { it.dead == 0 }
        eventually("retried swipe sent") { fake.swipeBatches == listOf(listOf(id)) }
    }

    @Test
    fun deadLettersFromAnOlderVersionGetOneMoreTry() = runTest {
        db.cards().addCards(listOf(card("a")))
        repository.swipe(card("a"), Action.SAVE, null)
        val id = db.outbox().pending(1).single().swipeId
        db.outbox().markDead(id, "HTTP 422: bad", appVersion = 0)  // the app is now version 1
        val viewModel = viewModel()

        viewModel.onForeground()

        eventually("swipe sent") { fake.swipeBatches == listOf(listOf(id)) }
        eventually("outbox empty") { db.outbox().deadLetters().isEmpty() && db.outbox().pending(10).isEmpty() }
    }
}
