package io.github.shanirub.swiperss.swipe

import io.github.shanirub.swiperss.api.Action
import io.github.shanirub.swiperss.api.Card
import io.github.shanirub.swiperss.api.Swipe
import io.github.shanirub.swiperss.api.SwipeRssApi
import io.github.shanirub.swiperss.db.AppDatabase
import kotlinx.coroutines.flow.Flow
import java.time.Clock
import java.time.OffsetDateTime
import java.time.format.DateTimeFormatter
import java.time.temporal.ChronoUnit
import java.util.UUID

// The swipe screen's data: the card stack in Room, refilled from GET /queue, and swipes recorded
// into the outbox (PROJECT_PLAN.md §3 Android). Sending the outbox is the sync step's job.
class SwipeRepository(
    private val api: SwipeRssApi,
    private val db: AppDatabase,
    private val appVersion: Int,
    private val clock: Clock = Clock.systemDefaultZone(),  // wall clock and time zone; fixed in tests
    private val newSwipeId: () -> String = { UUID.randomUUID().toString() },
) {
    val cards: Flow<List<Card>> = db.cards().observeCards()

    // Asks the server for cards only when the stack runs low. Throws on failure (IOException:
    // no connection; HttpException: an error status; SerializationException: an unreadable answer).
    suspend fun refill() {
        if (db.cards().count() < REFILL_BELOW) {
            db.cards().addCards(api.queue(limit = QUEUE_LIMIT).items)
        }
    }

    suspend fun swipe(card: Card, action: Action, timeToSwipeMs: Long?) {
        // One clock reading for both fields, so the time and the offset always belong together.
        val now = OffsetDateTime.now(clock).truncatedTo(ChronoUnit.MILLIS)
        db.outbox().recordSwipe(
            Swipe(
                swipeId = newSwipeId(),
                action = action,
                swipedAt = now.format(DateTimeFormatter.ISO_OFFSET_DATE_TIME),
                tzOffsetMinutes = now.offset.totalSeconds / 60,
                timeToSwipeMs = timeToSwipeMs,
                appVersion = appVersion,
                card = card,
            )
        )
    }

    companion object {
        const val REFILL_BELOW = 10
        const val QUEUE_LIMIT = 50  // the server's default
    }
}
