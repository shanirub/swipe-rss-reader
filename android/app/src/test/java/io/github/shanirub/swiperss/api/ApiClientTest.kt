package io.github.shanirub.swiperss.api

import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import mockwebserver3.MockResponse
import mockwebserver3.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test
import retrofit2.HttpException

// The API client against MockWebServer, a fake HTTP server in the test: each test checks the exact
// request the app sends (path, token, JSON body) and how it reads canned answers.
class ApiClientTest {
    private val token = "test-token-0123456789-0123456789-abcdef"
    private lateinit var server: MockWebServer
    private lateinit var api: SwipeRssApi

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
        api = ApiClient.create(server.url("/").toString(), token)
    }

    @After
    fun tearDown() = server.close()

    private fun respond(code: Int, body: String) =
        server.enqueue(
            MockResponse.Builder()
                .code(code)
                .addHeader("Content-Type", "application/json")
                .body(body)
                .build()
        )

    private val itemKey = "guid:" + "0123456789abcdef".repeat(4)

    // A card as the queue serves it, with null in every nullable field.
    private val cardJson = """
        {"feed_id": "eff", "item_key": "$itemKey", "headline": "Headline", "summary": "",
         "link": null, "published_at": null, "fetched_at": "2026-10-11T12:00:00Z",
         "author": null, "tags": []}
    """

    @Test
    fun queueSendsTokenAndLimitAndReadsNulls() = runTest {
        respond(200, """{"items": [$cardJson]}""")

        val cards = api.queue(limit = 50).items

        val request = server.takeRequest()
        assertEquals("GET", request.method)
        assertEquals("/queue?limit=50", request.target)
        assertEquals("Bearer $token", request.headers["Authorization"])
        val card = cards.single()
        assertEquals(itemKey, card.itemKey)
        assertNull(card.link)
        assertNull(card.publishedAt)
        assertNull(card.author)
        assertEquals(emptyList<String>(), card.tags)
    }

    @Test
    fun postSwipesSendsEveryFieldIncludingNulls() = runTest {
        respond(200, """{"stored": 1, "duplicates": 0}""")
        val card = apiJson.decodeFromString(Card.serializer(), cardJson)
        val swipe = Swipe(
            swipeId = "3f1c8a52-7d0e-4b8e-9a51-2c6f0e9d4b17",
            action = Action.READ_NOW,
            swipedAt = "2026-10-11T14:30:00+03:00",
            tzOffsetMinutes = 180,
            timeToSwipeMs = null,
            appVersion = 1,
            card = card,
        )

        val result = api.postSwipes(SwipeBatch(listOf(swipe)))

        val request = server.takeRequest()
        assertEquals("POST", request.method)
        assertEquals("/swipes", request.target)
        assertEquals("Bearer $token", request.headers["Authorization"])
        assertEquals("application/json", request.headers["Content-Type"]?.substringBefore(';'))
        // The spec's field names, every key present (required but nullable), the card unchanged.
        val expected = """
            {"swipes": [{"swipe_id": "3f1c8a52-7d0e-4b8e-9a51-2c6f0e9d4b17", "action": "read_now",
              "swiped_at": "2026-10-11T14:30:00+03:00", "tz_offset_minutes": 180,
              "time_to_swipe_ms": null, "app_version": 1, "card": $cardJson}]}
        """
        assertEquals(
            Json.parseToJsonElement(expected),
            Json.parseToJsonElement(request.body!!.utf8()),
        )
        assertEquals(SwipeBatchResult(stored = 1, duplicates = 0), result)
    }

    @Test
    fun actionsUseTheSpecNames() {
        assertEquals(
            listOf("\"never\"", "\"save\"", "\"read_now\""),
            Action.entries.map { apiJson.encodeToString(Action.serializer(), it) },
        )
    }

    @Test
    fun feedsReadsNeverFetchedFeeds() = runTest {
        respond(
            200,
            """{"feeds": [{"feed_id": "eff", "name": null, "url": "https://www.eff.org/rss",
               "last_attempt_at": null, "last_success_at": null, "last_new_item_at": null,
               "last_error": null, "consecutive_failures": 0}]}""",
        )

        val feed = api.feeds().feeds.single()

        assertEquals("/feeds", server.takeRequest().target)
        assertEquals("eff", feed.feedId)
        assertNull(feed.name)
        assertNull(feed.lastSuccessAt)
        assertEquals(0, feed.consecutiveFailures)
    }

    @Test
    fun unknownResponseFieldsAreIgnored() = runTest {
        respond(200, """{"items": [], "added_later": {"x": 1}}""")

        assertEquals(emptyList<Card>(), api.queue(limit = 1).items)
    }

    @Test
    fun errorStatusesThrowWithTheirCode() = runTest {
        for (code in listOf(401, 422, 503)) {
            respond(code, """{"detail": "error"}""")
            try {
                api.feeds()
                fail("expected HttpException for $code")
            } catch (e: HttpException) {
                assertEquals(code, e.code())
            }
        }
    }
}
