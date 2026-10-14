package io.github.shanirub.swiperss.swipe

import io.github.shanirub.swiperss.api.Action
import io.github.shanirub.swiperss.api.ApiClient
import io.github.shanirub.swiperss.api.Swipe
import io.github.shanirub.swiperss.api.apiJson
import io.github.shanirub.swiperss.db.inMemoryDatabase
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.SerializationException
import mockwebserver3.MockResponse
import mockwebserver3.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import retrofit2.HttpException
import java.time.Clock
import java.time.Instant
import java.time.ZoneId

// The swipe screen's data layer against MockWebServer and an in-memory Room database.
class SwipeRepositoryTest {
    private val server = MockWebServer().apply { start() }
    private val api = ApiClient.create(server.url("/").toString(), TEST_TOKEN)
    private val db = inMemoryDatabase()

    @After
    fun close() {
        db.close()
        server.close()
    }

    private fun repository(clock: Clock = Clock.systemUTC()) =
        SwipeRepository(api, db, appVersion = 7, clock = clock, newSwipeId = { "0b7e1d2c-5f1a-4c3b-9d2e-8a6f4b3c2d1e" })

    // Jerusalem is at UTC+3 on this day (summer time).
    private fun clockAt(instant: String, zone: String) = Clock.fixed(Instant.parse(instant), ZoneId.of(zone))

    private suspend fun stackKeys() = db.cards().observeCards().first().map { it.itemKey }

    private suspend fun recordedSwipe(): Swipe = apiJson.decodeFromString(db.outbox().pending(10).single().swipeJson)

    @Test
    fun refillAsksForCardsWhenTheStackIsLow() = runTest {
        db.cards().addCards((1..9).map { card("$it") })
        server.enqueue(queueResponse(card("1"), card("10"), card("11")))

        repository().refill()

        assertEquals("/queue?limit=50", server.takeRequest().target)
        assertEquals((1..11).map { "$it" }, stackKeys())
    }

    @Test
    fun refillLeavesAFullStackAlone() = runTest {
        db.cards().addCards((1..10).map { card("$it") })

        repository().refill()

        assertEquals(0, server.requestCount)
    }

    @Test
    fun refillFailuresReachTheCaller() = runTest {
        // The view model turns these into a message; swallowing them here would hide them.
        server.enqueue(MockResponse.Builder().code(503).build())
        try {
            repository().refill()
            fail("expected HttpException")
        } catch (e: HttpException) {
            assertEquals(503, e.code())
        }
        server.enqueue(MockResponse.Builder().code(200).body("""{"items": [{"feed_id": 1}]}""").build())
        try {
            repository().refill()
            fail("expected SerializationException")
        } catch (e: SerializationException) {
        }
        assertEquals(emptyList<String>(), stackKeys())
    }

    @Test
    fun aSwipeRecordsTheCardWithLocalTimeAndOffset() = runTest {
        db.cards().addCards(listOf(card("a"), card("b")))

        repository(clockAt("2026-10-13T07:05:00.123456Z", "Asia/Jerusalem")).swipe(card("a"), Action.SAVE, 2500)

        val expected = Swipe(
            swipeId = "0b7e1d2c-5f1a-4c3b-9d2e-8a6f4b3c2d1e",
            action = Action.SAVE,
            swipedAt = "2026-10-13T10:05:00.123+03:00",  // phone time with its offset, to the millisecond
            tzOffsetMinutes = 180,
            timeToSwipeMs = 2500,
            appVersion = 7,
            card = card("a"),
        )
        assertEquals(expected, recordedSwipe())
        assertEquals(listOf("b"), stackKeys())
    }

    @Test
    fun swipeTimesAreValidForEveryOffset() = runTest {
        // West of UTC, UTC itself, and a whole second: RFC 3339 requires the seconds.
        val cases = listOf(
            Triple(clockAt("2026-10-13T07:05:00.5Z", "America/New_York"), "2026-10-13T03:05:00.5-04:00", -240),
            Triple(clockAt("2026-10-13T07:05:00Z", "UTC"), "2026-10-13T07:05:00Z", 0),
            Triple(clockAt("2026-10-13T07:05:00Z", "Asia/Kolkata"), "2026-10-13T12:35:00+05:30", 330),
        )
        for ((clock, swipedAt, offset) in cases) {
            db.cards().addCards(listOf(card("a")))
            repository(clock).swipe(card("a"), Action.NEVER, null)

            val swipe = recordedSwipe()
            assertEquals(swipedAt, swipe.swipedAt)
            assertEquals(offset, swipe.tzOffsetMinutes)
            db.outbox().delete(listOf(swipe.swipeId))
        }
    }

    @Test
    fun refillAndSyncNeverOverlap() = runTest {
        // A /queue answer built before the server got a swipe must not arrive after sync has
        // removed that swipe from the outbox (the card would come back). So sync waits.
        val fake = FakeServer()
        server.dispatcher = fake
        fake.queueAnswers.add(queueResponse(card("x"), delayMs = 300))
        db.cards().addCards(listOf(card("a")))
        val repository = repository()
        repository.swipe(card("a"), Action.SAVE, null)

        coroutineScope {
            launch(Dispatchers.IO) { repository.refill() }
            eventually("refill started") { fake.arrivals.isNotEmpty() }
            launch(Dispatchers.IO) { repository.sync() }
        }

        val waitedMs = (fake.arrival("/swipes") - fake.arrival("/queue")) / 1_000_000
        assertTrue("sync started ${waitedMs} ms after the refill", waitedMs >= 300)
        assertEquals(listOf("x"), stackKeys())
    }
}
