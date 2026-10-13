package io.github.shanirub.swiperss.swipe

import io.github.shanirub.swiperss.api.Card
import io.github.shanirub.swiperss.api.QueueResponse
import io.github.shanirub.swiperss.api.apiJson
import mockwebserver3.MockResponse

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

fun queueResponse(vararg cards: Card): MockResponse =
    MockResponse.Builder()
        .code(200)
        .addHeader("Content-Type", "application/json")
        .body(apiJson.encodeToString(QueueResponse(cards.toList())))
        .build()

const val TEST_TOKEN = "test-token-0123456789-0123456789-abcdef"
