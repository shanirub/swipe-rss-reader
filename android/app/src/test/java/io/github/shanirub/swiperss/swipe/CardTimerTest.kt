package io.github.shanirub.swiperss.swipe

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class CardTimerTest {
    private var now = 0L
    private val timer = CardTimer { now }

    @Test
    fun measuresFromWhenTheCardBecameTheTopCard() {
        now = 1000
        timer.show(card("a"))
        now = 3500
        assertEquals(2500L, timer.elapsedFor(card("a")))
    }

    @Test
    fun theSameTopCardAgainKeepsItsTime() {
        // The stack changes (cards added at the end) while the top card stays.
        now = 1000
        timer.show(card("a"))
        now = 2000
        timer.show(card("a"))
        now = 3500
        assertEquals(2500L, timer.elapsedFor(card("a")))
    }

    @Test
    fun aNewTopCardStartsAgain() {
        timer.show(card("a"))
        now = 1000
        timer.show(card("b"))
        now = 1200
        assertEquals(200L, timer.elapsedFor(card("b")))
    }

    @Test
    fun timeInAnotherAppDoesNotCount() {
        timer.show(card("a"))
        now = 60_000
        timer.restart()
        now = 61_500
        assertEquals(1500L, timer.elapsedFor(card("a")))
    }

    @Test
    fun aCardThatIsNotTimedGetsNoTime() {
        assertNull(timer.elapsedFor(card("a")))
        timer.show(card("a"))
        assertNull(timer.elapsedFor(card("b")))
        timer.show(null)  // empty stack
        assertNull(timer.elapsedFor(card("a")))
    }
}
