package io.github.shanirub.swiperss.swipe

import io.github.shanirub.swiperss.api.Action
import io.github.shanirub.swiperss.api.ApiClient
import io.github.shanirub.swiperss.api.Swipe
import io.github.shanirub.swiperss.api.apiJson
import io.github.shanirub.swiperss.db.inMemoryDatabase
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import mockwebserver3.MockResponse
import mockwebserver3.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test

// The view model's wiring: refill errors become a message, and swipes get the card timer's time.
// viewModelScope runs on Android's main thread, which the JVM lacks; the test dispatcher stands in.
@OptIn(ExperimentalCoroutinesApi::class)
class SwipeViewModelTest {
    private val server = MockWebServer().apply { start() }
    private val db = inMemoryDatabase()
    private val repository = SwipeRepository(ApiClient.create(server.url("/").toString(), TEST_TOKEN), db, appVersion = 1)

    @Volatile
    private var now = 0L

    @Before
    fun setMain() = Dispatchers.setMain(UnconfinedTestDispatcher())

    @After
    fun tearDown() {
        Dispatchers.resetMain()
        db.close()
        server.close()
    }

    private fun viewModel() = SwipeViewModel(repository) { now }

    @Test
    fun aFailedRefillShowsWhyUntilOneSucceeds() = runTest {
        val viewModel = viewModel()
        server.enqueue(MockResponse.Builder().code(503).build())
        viewModel.onForeground()
        assertEquals("Server error 503", viewModel.state.first { !it.loading && it.problem != null }.problem)

        server.enqueue(queueResponse(card("a")))
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
        val viewModel = viewModel()
        now = 1000
        server.enqueue(queueResponse(card("a"), card("b")))
        viewModel.onForeground()
        viewModel.state.first { it.cards == listOf(card("a"), card("b")) }

        now = 3500
        server.enqueue(queueResponse())  // the refill after the swipe
        viewModel.swipe(card("a"), Action.SAVE)
        viewModel.state.first { it.cards == listOf(card("b")) }
        // b became the top card at 3500, then the app was in the background until 9000: that doesn't count.
        now = 9000
        server.enqueue(queueResponse())
        viewModel.onForeground()
        now = 9800
        server.enqueue(queueResponse())
        viewModel.swipe(card("b"), Action.NEVER)
        viewModel.state.first { it.cards == emptyList<Any>() }

        val times = db.outbox().pending(10)
            .map { apiJson.decodeFromString<Swipe>(it.swipeJson) }
            .associate { it.card.itemKey to it.timeToSwipeMs }
        assertEquals(mapOf("a" to 2500L, "b" to 800L), times)
    }
}
