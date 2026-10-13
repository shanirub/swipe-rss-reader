package io.github.shanirub.swiperss.swipe

import io.github.shanirub.swiperss.api.Action
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class SwipeGestureTest {
    // A screen 300 px wide and 600 px high: a third is 100 and 200 px.
    private fun action(dx: Float, dy: Float, vx: Float = 0f, vy: Float = 0f, canReadNow: Boolean = true) =
        swipeAction(dx, dy, vx, vy, minDistanceX = 100f, minDistanceY = 200f, flingSpeed = 1000f, canReadNow = canReadNow)

    @Test
    fun farEnoughInADirectionIsThatAction() {
        assertEquals(Action.SAVE, action(dx = 101f, dy = 20f))
        assertEquals(Action.NEVER, action(dx = -101f, dy = -20f))
        assertEquals(Action.READ_NOW, action(dx = 20f, dy = -201f))
    }

    @Test
    fun aShortDragSpringsBack() {
        assertNull(action(dx = 99f, dy = 0f))
        assertNull(action(dx = -99f, dy = 0f))
        assertNull(action(dx = 0f, dy = -199f))
        assertNull(action(dx = 0f, dy = 0f))
    }

    @Test
    fun aFastFlickCountsEvenIfShort() {
        assertEquals(Action.SAVE, action(dx = 30f, dy = 0f, vx = 1500f))
        assertEquals(Action.NEVER, action(dx = -30f, dy = 0f, vx = -1500f))
        assertEquals(Action.READ_NOW, action(dx = 0f, dy = -30f, vy = -1500f))
    }

    @Test
    fun aFlickAgainstTheDragDoesNot() {
        // Dragged right, then flicked back left while letting go: the user changed their mind.
        assertNull(action(dx = 30f, dy = 0f, vx = -1500f))
        assertNull(action(dx = 0f, dy = -30f, vy = 1500f))
        assertNull(action(dx = 0f, dy = 30f, vy = -1500f))
    }

    @Test
    fun theLongerAxisDecides() {
        assertEquals(Action.SAVE, action(dx = 250f, dy = -240f))
        assertEquals(Action.READ_NOW, action(dx = 150f, dy = -250f))
    }

    @Test
    fun downMeansNothing() {
        assertNull(action(dx = 0f, dy = 400f))
        assertNull(action(dx = 0f, dy = 50f, vy = 3000f))
    }

    @Test
    fun noReadNowWithoutALink() {
        assertNull(action(dx = 0f, dy = -400f, canReadNow = false))
        assertEquals(Action.SAVE, action(dx = 200f, dy = 0f, canReadNow = false))
    }

    @Test
    fun onlyWebLinksCanBeRead() {
        assertEquals("https://example.com/a", readableLink(card("a")))
        assertEquals("HTTP://example.com/a", readableLink(card("a", link = "HTTP://example.com/a")))
        assertNull(readableLink(card("a", link = null)))
        assertNull(readableLink(card("a", link = "mailto:me@example.com")))
        assertNull(readableLink(card("a", link = "javascript:alert(1)")))
        assertNull(readableLink(card("a", link = "example.com/a")))
    }
}
