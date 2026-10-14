package io.github.shanirub.swiperss.db

import io.github.shanirub.swiperss.api.Action
import io.github.shanirub.swiperss.api.Card
import io.github.shanirub.swiperss.api.Swipe
import io.github.shanirub.swiperss.api.apiJson
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.test.runTest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class LocalStoreTest {
    private val db = inMemoryDatabase()  // TestDatabase.kt
    private val cards = db.cards()
    private val outbox = db.outbox()

    @After
    fun close() = db.close()

    private fun card(key: String, feed: String = "feed") = Card(
        feedId = feed,
        itemKey = key,
        headline = "Headline $key",
        summary = "Summary",
        link = null,
        publishedAt = null,
        fetchedAt = "2026-10-12T10:00:00Z",
        author = null,
        tags = listOf("a", "b"),
    )

    private fun swipe(card: Card, id: String = "swipe-${card.itemKey}") = Swipe(
        swipeId = id,
        action = Action.SAVE,
        swipedAt = "2026-10-12T10:05:00Z",
        tzOffsetMinutes = 180,
        timeToSwipeMs = null,
        appVersion = 1,
        card = card,
    )

    private suspend fun stackKeys() = cards.observeCards().first().map { it.itemKey }

    @Test
    fun newCardsKeepTheirOrderAndKnownOnesAreSkipped() = runTest {
        cards.addCards(listOf(card("a"), card("b")))
        // /queue resends cards the phone already holds; the same key in another feed is a new card.
        cards.addCards(listOf(card("b"), card("c"), card("a"), card("c"), card("a", feed = "other")))

        assertEquals(listOf("a", "b", "c", "a"), stackKeys())
        assertEquals(4, cards.count())
    }

    @Test
    fun cardsComeBackUnchanged() = runTest {
        val full = card("a").copy(link = "https://example.com/a", publishedAt = "2026-10-12T09:00:00Z", author = "Me")
        cards.addCards(listOf(card("n"), full))

        assertEquals(listOf(card("n"), full), cards.observeCards().first())
    }

    @Test
    fun aSwipeMovesTheCardToTheOutbox() = runTest {
        cards.addCards(listOf(card("a"), card("b")))
        val swipe = swipe(card("a"))

        outbox.recordSwipe(swipe)

        assertEquals(listOf("b"), stackKeys())
        val pending = outbox.pending(limit = 10)
        assertEquals(listOf("swipe-a"), pending.map { it.swipeId })
        assertEquals(swipe, apiJson.decodeFromString<Swipe>(pending.single().swipeJson))
    }

    @Test
    fun aFailedSwipeLeavesTheCardOnTheStack() = runTest {
        cards.addCards(listOf(card("a"), card("b")))
        outbox.recordSwipe(swipe(card("b"), id = "same-id"))

        // A second swipe with the same id breaks the insert; the card's delete must roll back too.
        val failed = runCatching { outbox.recordSwipe(swipe(card("a"), id = "same-id")) }

        assertTrue(failed.isFailure)
        assertEquals(listOf("a"), stackKeys())
    }

    @Test
    fun swipedCardsAreNotAddedAgain() = runTest {
        cards.addCards(listOf(card("a"), card("b")))
        outbox.recordSwipe(swipe(card("a")))
        outbox.recordSwipe(swipe(card("b")))
        outbox.markDead("swipe-b", "422", appVersion = 1)

        // The server keeps serving them until it has the swipes, pending or dead letter alike.
        cards.addCards(listOf(card("a"), card("b"), card("c")))

        assertEquals(listOf("c"), stackKeys())
    }

    @Test
    fun pendingSkipsDeadLettersAndDeleteRemovesOnlyTheSentSwipes() = runTest {
        for (key in listOf("a", "b", "c", "d")) outbox.recordSwipe(swipe(card(key)))
        outbox.markDead("swipe-d", "422: tz_offset_minutes", appVersion = 1)

        assertEquals(setOf("swipe-a", "swipe-b", "swipe-c"), outbox.pending(limit = 10).map { it.swipeId }.toSet())
        assertEquals(2, outbox.pending(limit = 2).size)

        outbox.delete(listOf("swipe-a", "swipe-b"))

        assertEquals(listOf("swipe-c"), outbox.pending(limit = 10).map { it.swipeId })
        val dead = outbox.deadLetters().single()
        assertEquals("swipe-d", dead.swipeId)
        assertEquals("422: tz_offset_minutes", dead.deadError)
        assertEquals(1, dead.deadAppVersion)
    }

    @Test
    fun deadLettersAreRetriedOncePerNewAppVersion() = runTest {
        outbox.recordSwipe(swipe(card("a")))
        outbox.markDead("swipe-a", "422", appVersion = 3)

        assertEquals(0, outbox.reviveDeadLettersOlderThan(3))  // same version: stays dead
        assertEquals(1, outbox.reviveDeadLettersOlderThan(4))  // new version: one more try
        val revived = outbox.pending(limit = 10).single()
        assertEquals("swipe-a", revived.swipeId)
        assertEquals(null, revived.deadError)
        assertEquals(null, revived.deadAppVersion)
        assertTrue(outbox.deadLetters().isEmpty())

        outbox.markDead("swipe-a", "422", appVersion = 4)  // failed again on version 4
        assertEquals(0, outbox.reviveDeadLettersOlderThan(4))
    }

    @Test
    fun theScreenSeesHowManySwipesArePendingAndDead() = runTest {
        cards.addCards(listOf(card("a"), card("b"), card("c")))
        listOf("a", "b", "c").forEach { outbox.recordSwipe(swipe(card(it))) }
        outbox.markDead("swipe-b", "HTTP 422: bad", appVersion = 1)

        assertEquals(2, outbox.observePendingCount().first())
        assertEquals(1, outbox.observeDeadCount().first())
    }

    @Test
    fun retryRevivesEveryDeadLetterWhateverItsVersion() = runTest {
        listOf("a", "b", "c").forEach { outbox.recordSwipe(swipe(card(it))) }
        outbox.markDead("swipe-a", "HTTP 422: bad", appVersion = 1)
        outbox.markDead("swipe-b", "HTTP 422: bad", appVersion = 5)

        assertEquals(2, outbox.reviveAllDeadLetters())

        assertEquals(listOf("swipe-a", "swipe-b", "swipe-c"), outbox.pending(10).map { it.swipeId }.sorted())
        assertTrue(outbox.pending(10).all { it.deadError == null && it.deadAppVersion == null })
    }
}
