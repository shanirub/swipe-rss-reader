package io.github.shanirub.swiperss.swipe

import io.github.shanirub.swiperss.api.Card
import io.github.shanirub.swiperss.api.QueueResponse
import io.github.shanirub.swiperss.api.apiJson
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import mockwebserver3.Dispatcher
import mockwebserver3.MockResponse
import mockwebserver3.RecordedRequest
import org.junit.Assert.fail
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.ConcurrentLinkedQueue
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.TimeUnit

fun card(key: String, link: String? = "https://example.com/$key") = Card(
    feedId = "feed",
    itemKey = key,
    headline = "Headline $key",
    summary = "Summary",
    link = link,
    publishedAt = null,
    fetchedAt = "2026-10-13T06:00:00Z",
    author = null,
    tags = emptyList(),
)

fun queueResponse(vararg cards: Card, delayMs: Long = 0): MockResponse =
    MockResponse.Builder()
        .code(200)
        .addHeader("Content-Type", "application/json")
        .body(apiJson.encodeToString(QueueResponse(cards.toList())))
        .headersDelay(delayMs, TimeUnit.MILLISECONDS)
        .build()

fun jsonResponse(code: Int, body: String): MockResponse =
    MockResponse.Builder().code(code).addHeader("Content-Type", "application/json").body(body).build()

fun swipesStored(n: Int) = jsonResponse(200, """{"stored": $n, "duplicates": 0}""")

const val TEST_TOKEN = "test-token-0123456789-0123456789-abcdef"

// A fake server that answers by path, for tests where cards are fetched and swipes sent at the
// same time. It keeps every POST /swipes body and when each request arrived.
class FakeServer : Dispatcher() {
    val queueAnswers = ConcurrentLinkedQueue<MockResponse>()  // none left: an empty queue

    // When set (and queueAnswers is empty), GET /queue behaves like the real server: the first
    // `limit` of these cards that no received swipe was for.
    @Volatile
    var items: List<Card>? = null

    // The answer to POST /swipes, given the swipe_ids of the batch.
    @Volatile
    var swipesAnswer: (List<String>) -> MockResponse = { swipesStored(it.size) }

    val swipeBodies = CopyOnWriteArrayList<String>()
    val arrivals = CopyOnWriteArrayList<Pair<String, Long>>()  // path, System.nanoTime()

    val swipeBatches: List<List<String>> get() = swipeBodies.map(::swipeIds)

    override fun dispatch(request: RecordedRequest): MockResponse {
        val path = request.url.encodedPath
        arrivals.add(path to System.nanoTime())
        return when (path) {
            "/queue" -> queueAnswers.poll() ?: unswiped(request.url.queryParameter("limit")!!.toInt())
            "/swipes" -> {
                val body = request.body!!.utf8()
                swipeBodies.add(body)
                swipesAnswer(swipeIds(body)).also { answer ->
                    if (answer.code == 200) swiped.addAll(swipes(body).map { it.getValue("card").jsonObject.getValue("item_key").jsonPrimitive.content })
                }
            }
            else -> MockResponse.Builder().code(404).build()
        }
    }

    fun arrival(path: String): Long = arrivals.first { it.first == path }.second

    private val swiped = ConcurrentHashMap.newKeySet<String>()  // item_keys of accepted swipes

    private fun unswiped(limit: Int): MockResponse =
        queueResponse(*items.orEmpty().filter { it.itemKey !in swiped }.take(limit).toTypedArray())

    private fun swipes(body: String): List<JsonObject> =
        Json.parseToJsonElement(body).jsonObject.getValue("swipes").jsonArray.map { it as JsonObject }

    private fun swipeIds(body: String): List<String> = swipes(body).map { it.getValue("swipe_id").jsonPrimitive.content }
}

// Waits in real time: Room and MockWebServer work on their own threads, which the coroutine test
// scheduler doesn't know about.
suspend fun eventually(what: String, condition: suspend () -> Boolean) = withContext(Dispatchers.IO) {
    repeat(500) {
        if (condition()) return@withContext
        Thread.sleep(10)
    }
    fail("timed out waiting for: $what")
}
