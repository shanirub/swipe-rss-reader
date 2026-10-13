package io.github.shanirub.swiperss.swipe

import io.github.shanirub.swiperss.api.Action
import io.github.shanirub.swiperss.api.Card
import kotlin.math.abs

// Which action a released drag means (PROJECT_PLAN.md §3 swipe semantics): left never, right save,
// up read now. Kept free of Compose so it runs in JVM tests. Distances and speeds are in pixels
// (per second); the screen passes its thresholds.
//
// A drag counts if it went far enough, or if it was flicked fast in the same direction. The
// direction is the axis the card moved further along; a drag downwards means nothing.
fun swipeAction(
    dx: Float,
    dy: Float,
    vx: Float,
    vy: Float,
    minDistanceX: Float,
    minDistanceY: Float,
    flingSpeed: Float,
    canReadNow: Boolean,
): Action? =
    if (abs(dx) >= abs(dy)) {
        val far = abs(dx) > minDistanceX
        val flung = abs(vx) > flingSpeed && (vx > 0) == (dx > 0)
        when {
            dx == 0f || !(far || flung) -> null
            dx > 0 -> Action.SAVE
            else -> Action.NEVER
        }
    } else {
        val up = dy < 0 && (-dy > minDistanceY || vy < -flingSpeed)
        if (up && canReadNow) Action.READ_NOW else null
    }

// The link "read now" may open: http(s) only. The fetcher stores feed links as given, so a feed
// could also send mailto:, javascript: or similar; null links exist too (missing, or too long).
fun readableLink(card: Card): String? =
    card.link?.takeIf { it.substringBefore(':').lowercase() in setOf("http", "https") }
