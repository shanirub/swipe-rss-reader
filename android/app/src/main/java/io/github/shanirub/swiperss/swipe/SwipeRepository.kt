package io.github.shanirub.swiperss.swipe

import io.github.shanirub.swiperss.api.Action
import io.github.shanirub.swiperss.api.Card
import io.github.shanirub.swiperss.api.Swipe
import io.github.shanirub.swiperss.api.SwipeRssApi
import io.github.shanirub.swiperss.db.AppDatabase
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import java.time.Clock
import java.time.OffsetDateTime
import java.time.format.DateTimeFormatter
import java.time.temporal.ChronoUnit
import java.util.UUID

// The swipe screen's data: the card stack in Room, refilled from GET /queue, and swipes recorded
// into the outbox and sent to POST /swipes (PROJECT_PLAN.md §3 Android).
class SwipeRepository(
    private val api: SwipeRssApi,
    private val db: AppDatabase,
    private val appVersion: Int,
    private val clock: Clock = Clock.systemDefaultZone(),  // wall clock and time zone; fixed in tests
    private val newSwipeId: () -> String = { UUID.randomUUID().toString() },
) {
    val cards: Flow<List<Card>> = db.cards().observeCards()
    val pendingCount: Flow<Int> = db.outbox().observePendingCount()
    val deadCount: Flow<Int> = db.outbox().observeDeadCount()

    private val sender = SwipeSync(api, db.outbox(), appVersion)

    // Refill and sync never overlap. Otherwise a /queue answer the server built before it got a
    // swipe could arrive after sync removed that swipe from the outbox, and the card would be
    // added to the stack again.
    private val network = Mutex()

    // Asks the server for cards only when the stack runs low. Throws on failure (IOException:
    // no connection; HttpException: an error status; SerializationException: an unreadable answer).
    suspend fun refill() = network.withLock {
        if (db.cards().count() < REFILL_BELOW) {
            db.cards().addCards(api.queue(limit = QUEUE_LIMIT).items)
        }
    }

    // Sends the pending swipes; throws like refill() when it has to stop (see SwipeSync).
    suspend fun sync() = network.withLock { sender.sendPending() }

    // Dead letters from older app versions get one more try (on the first sync after an update).
    suspend fun reviveOlderDeadLetters() = db.outbox().reviveDeadLettersOlderThan(appVersion)

    suspend fun reviveAllDeadLetters() = db.outbox().reviveAllDeadLetters()

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
