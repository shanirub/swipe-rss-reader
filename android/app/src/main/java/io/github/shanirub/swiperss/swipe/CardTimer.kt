package io.github.shanirub.swiperss.swipe

import io.github.shanirub.swiperss.api.Card

// Measures time_to_swipe_ms: how long the top card has been on screen (PROJECT_PLAN.md §3 swipe log
// fields). `now` is a monotonic clock in milliseconds (SystemClock.elapsedRealtime in the app), so
// a wall-clock change can't make a time negative.
class CardTimer(private val now: () -> Long) {
    private var key: Pair<String, String>? = null
    private var since = 0L

    // Called with the top card after every change to the stack. Only a different card restarts the
    // timer: the stack also changes when cards are added at the end.
    fun show(card: Card?) {
        val newKey = card?.let { it.feedId to it.itemKey }
        if (newKey != key) {
            key = newKey
            since = now()
        }
    }

    // When the app comes back to the foreground: time spent in another app doesn't count.
    fun restart() {
        since = now()
    }

    // null if the card isn't the one being timed (no measurement rather than a wrong one).
    fun elapsedFor(card: Card): Long? =
        if (key == (card.feedId to card.itemKey)) now() - since else null
}
