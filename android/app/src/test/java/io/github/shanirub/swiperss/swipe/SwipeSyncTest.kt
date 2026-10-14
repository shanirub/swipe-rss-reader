package io.github.shanirub.swiperss.swipe

import io.github.shanirub.swiperss.api.Action
import io.github.shanirub.swiperss.api.ApiClient
import io.github.shanirub.swiperss.api.Swipe
import io.github.shanirub.swiperss.db.inMemoryDatabase
import kotlinx.coroutines.test.runTest
import mockwebserver3.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import retrofit2.HttpException
import java.io.IOException

// Sending the outbox to POST /swipes, against a fake server and an in-memory database.
class SwipeSyncTest {
    private val fake = FakeServer()
    private val server = MockWebServer().apply { dispatcher = fake; start() }
    private val db = inMemoryDatabase()
    private val outbox = db.outbox()
    private val sync = SwipeSync(ApiClient.create(server.url("/").toString(), TEST_TOKEN), outbox, appVersion = 7)

    @After
    fun close() {
        db.close()
        server.close()
    }

    private suspend fun record(vararg ids: String) {
        for (id in ids) {
            outbox.recordSwipe(Swipe(id, Action.SAVE, "2026-10-13T10:05:00+03:00", 180, 1200, 1, card(id)))
        }
    }

    private suspend fun pendingIds() = outbox.pending(1000).map { it.swipeId }.sorted()

    private suspend inline fun <reified E : Exception> assertSyncThrows(): E {
        try {
            sync.sendPending()
        } catch (e: Exception) {
            if (e is E) return e
            throw e
        }
        fail("expected ${E::class.simpleName}")
        throw AssertionError()
    }

    @Test
    fun pendingSwipesAreSentAsStoredAndThenDeleted() = runTest {
        record("s1", "s2")
        val stored = outbox.pending(10).map { it.swipeJson }

        sync.sendPending()

        // The stored JSON goes out unchanged, not decoded and encoded again.
        val body = fake.swipeBodies.single()
        stored.forEach { assertTrue("sent as stored: $it", body.contains(it)) }
        assertEquals(listOf("s1", "s2"), fake.swipeBatches.single().sorted())
        assertEquals(emptyList<String>(), pendingIds())
    }

    @Test
    fun aLargeOutboxGoesInBatchesOf500() = runTest {
        record(*Array(501) { "s$it" })

        sync.sendPending()

        assertEquals(listOf(1, 500), fake.swipeBatches.map { it.size }.sorted())
        assertEquals(emptyList<String>(), pendingIds())
    }

    @Test
    fun failuresStopSendingAndKeepEverything() = runTest {
        record("s1", "s2")
        for (code in listOf(503, 500, 401)) {
            fake.swipesAnswer = { jsonResponse(code, """{"detail": "no"}""") }
            assertEquals(code, assertSyncThrows<HttpException>().code())
        }
        server.close()
        assertSyncThrows<IOException>()

        assertEquals(listOf("s1", "s2"), pendingIds())
        assertEquals(emptyList<Any>(), outbox.deadLetters())
    }

    @Test
    fun aRejectedBatchIsSentOneByOneAndOnlyTheBadSwipeIsKept() = runTest {
        record("s1", "s2", "s3")
        // The server rejects any batch that contains s2, like a batch with one invalid swipe.
        fake.swipesAnswer = { ids ->
            if ("s2" in ids) jsonResponse(422, """{"detail": [{"msg": "s2 is broken"}]}""") else swipesStored(ids.size)
        }

        sync.sendPending()

        assertEquals(listOf(3, 1, 1, 1), fake.swipeBatches.map { it.size })
        assertEquals(emptyList<String>(), pendingIds())
        val dead = outbox.deadLetters().single()
        assertEquals("s2", dead.swipeId)
        val error = dead.deadError!!
        assertTrue(error, error.startsWith("HTTP 422: ") && "s2 is broken" in error)
        assertEquals(7, dead.deadAppVersion)
    }

    @Test
    fun aFailureWhileSendingOneByOneStopsWithoutDeadLetters() = runTest {
        record("s1", "s2", "s3")
        var calls = 0
        fake.swipesAnswer = { ids ->
            calls++
            when {
                ids.size > 1 -> jsonResponse(422, """{"detail": []}""")
                calls == 2 -> swipesStored(1)  // the first single gets in, then the connection breaks
                else -> jsonResponse(503, "{}")
            }
        }

        assertEquals(503, assertSyncThrows<HttpException>().code())

        assertEquals(2, pendingIds().size)
        assertEquals(emptyList<Any>(), outbox.deadLetters())
    }

    @Test
    fun deadLettersAreNotSent() = runTest {
        record("s1", "s2")
        outbox.markDead("s1", "HTTP 422: old", appVersion = 7)

        sync.sendPending()

        assertEquals(listOf(listOf("s2")), fake.swipeBatches)
        assertEquals("s1", outbox.deadLetters().single().swipeId)
    }

    @Test
    fun nothingPendingSendsNothing() = runTest {
        sync.sendPending()
        assertNull(fake.arrivals.firstOrNull())
    }
}
