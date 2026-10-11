package io.github.shanirub.swiperss.api

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

// Wire models for the endpoints stage 5 uses, hand-written from api/openapi.yaml (PROJECT_PLAN.md
// §3 Android). Every field is required but nullable on the server, so no field has a default value:
// a forgotten field is a compile error here, not a silently missing key on the wire.
//
// Timestamps stay strings: a card goes back to the server unchanged inside each swipe, and keeping
// the received text avoids any re-formatting on the way.

@Serializable
data class Card(
    @SerialName("feed_id") val feedId: String,
    @SerialName("item_key") val itemKey: String,
    val headline: String,
    val summary: String,
    val link: String?,
    @SerialName("published_at") val publishedAt: String?,
    @SerialName("fetched_at") val fetchedAt: String,
    val author: String?,
    val tags: List<String>,
)

@Serializable
data class QueueResponse(val items: List<Card>)

@Serializable
enum class Action {
    @SerialName("never") NEVER,
    @SerialName("save") SAVE,
    @SerialName("read_now") READ_NOW,
}

@Serializable
data class Swipe(
    @SerialName("swipe_id") val swipeId: String,
    val action: Action,
    @SerialName("swiped_at") val swipedAt: String,
    @SerialName("tz_offset_minutes") val tzOffsetMinutes: Int,
    @SerialName("time_to_swipe_ms") val timeToSwipeMs: Long?,
    @SerialName("app_version") val appVersion: Int,
    val card: Card,
)

@Serializable
data class SwipeBatch(val swipes: List<Swipe>)

@Serializable
data class SwipeBatchResult(val stored: Int, val duplicates: Int)

@Serializable
data class FeedStatus(
    @SerialName("feed_id") val feedId: String,
    val name: String?,
    val url: String,
    @SerialName("last_attempt_at") val lastAttemptAt: String?,
    @SerialName("last_success_at") val lastSuccessAt: String?,
    @SerialName("last_new_item_at") val lastNewItemAt: String?,
    @SerialName("last_error") val lastError: String?,
    @SerialName("consecutive_failures") val consecutiveFailures: Int,
)

@Serializable
data class FeedListResponse(val feeds: List<FeedStatus>)
