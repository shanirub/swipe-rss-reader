package io.github.shanirub.swiperss.db

import android.content.Context
import androidx.room.ColumnInfo
import androidx.room.Dao
import androidx.room.Database
import androidx.room.Entity
import androidx.room.Insert
import androidx.room.PrimaryKey
import androidx.room.Query
import androidx.room.Room
import androidx.room.RoomDatabase
import androidx.room.Transaction
import io.github.shanirub.swiperss.api.Card
import io.github.shanirub.swiperss.api.Swipe
import io.github.shanirub.swiperss.api.apiJson
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

// The phone's local storage (PROJECT_PLAN.md §3 Android): the cards still to show and the swipes
// the server hasn't accepted yet. Cards and swipes are stored as the JSON received from and sent to
// the API, so a swipe is resent exactly as recorded; only the columns the queries need are separate.

@Entity(tableName = "cards", primaryKeys = ["feed_id", "item_key"])
data class CardRow(
    @ColumnInfo(name = "feed_id") val feedId: String,
    @ColumnInfo(name = "item_key") val itemKey: String,
    val position: Long,  // order received; the stack shows the lowest first
    @ColumnInfo(name = "card_json") val cardJson: String,
)

@Entity(tableName = "outbox")
data class OutboxRow(
    @PrimaryKey @ColumnInfo(name = "swipe_id") val swipeId: String,
    @ColumnInfo(name = "feed_id") val feedId: String,
    @ColumnInfo(name = "item_key") val itemKey: String,
    @ColumnInfo(name = "swipe_json") val swipeJson: String,
    // null = pending. Set = dead letter: the server rejected this swipe on its own (PROJECT_PLAN.md
    // §3 "Invalid swipes"); it is kept, not resent, until a newer app version retries it.
    @ColumnInfo(name = "dead_error") val deadError: String?,
    @ColumnInfo(name = "dead_app_version") val deadAppVersion: Int?,
)

@Dao
interface CardDao {
    @Query("SELECT card_json FROM cards ORDER BY position")
    fun observeCardJson(): Flow<List<String>>

    // Emits the stack again after every change to the cards table.
    fun observeCards(): Flow<List<Card>> =
        observeCardJson().map { rows -> rows.map { apiJson.decodeFromString<Card>(it) } }

    @Query("SELECT COUNT(*) FROM cards")
    suspend fun count(): Int

    @Query("SELECT COALESCE(MAX(position), 0) FROM cards")
    suspend fun maxPosition(): Long

    // Known = still on the stack, or swiped (pending or dead letter) and so not to be shown again.
    @Query(
        "SELECT EXISTS(SELECT 1 FROM cards WHERE feed_id = :feedId AND item_key = :itemKey) " +
            "OR EXISTS(SELECT 1 FROM outbox WHERE feed_id = :feedId AND item_key = :itemKey)"
    )
    suspend fun isKnown(feedId: String, itemKey: String): Boolean

    @Insert
    suspend fun insert(row: CardRow)

    // Cards from GET /queue. The server resends cards the phone already holds or whose swipes it
    // hasn't received yet (PROJECT_PLAN.md §3 queue semantics), so those are skipped; new cards go
    // to the end of the stack in the order received.
    @Transaction
    suspend fun addCards(cards: List<Card>) {
        var next = maxPosition() + 1
        for (card in cards) {
            if (!isKnown(card.feedId, card.itemKey)) {
                insert(CardRow(card.feedId, card.itemKey, next++, apiJson.encodeToString(card)))
            }
        }
    }
}

@Dao
interface OutboxDao {
    @Insert
    suspend fun insert(row: OutboxRow)

    @Query("DELETE FROM cards WHERE feed_id = :feedId AND item_key = :itemKey")
    suspend fun deleteCard(feedId: String, itemKey: String)

    // One transaction: the card leaves the stack exactly when its swipe is queued, so a crash can
    // neither lose the swipe nor show the card again.
    @Transaction
    suspend fun recordSwipe(swipe: Swipe) {
        deleteCard(swipe.card.feedId, swipe.card.itemKey)
        insert(
            OutboxRow(
                swipeId = swipe.swipeId,
                feedId = swipe.card.feedId,
                itemKey = swipe.card.itemKey,
                swipeJson = apiJson.encodeToString(swipe),
                deadError = null,
                deadAppVersion = null,
            )
        )
    }

    // Unordered: the server orders swipes by their swiped_at.
    @Query("SELECT * FROM outbox WHERE dead_error IS NULL LIMIT :limit")
    suspend fun pending(limit: Int): List<OutboxRow>

    // After the server accepted them (200).
    @Query("DELETE FROM outbox WHERE swipe_id IN (:swipeIds)")
    suspend fun delete(swipeIds: List<String>)

    @Query("UPDATE outbox SET dead_error = :error, dead_app_version = :appVersion WHERE swipe_id = :swipeId")
    suspend fun markDead(swipeId: String, error: String, appVersion: Int)

    @Query("SELECT * FROM outbox WHERE dead_error IS NOT NULL")
    suspend fun deadLetters(): List<OutboxRow>

    // On app start: dead letters from older versions get one more try. One that fails again is
    // marked with the current version, so it waits for the next release instead of looping.
    @Query("UPDATE outbox SET dead_error = NULL, dead_app_version = NULL WHERE dead_app_version < :appVersion")
    suspend fun reviveDeadLettersOlderThan(appVersion: Int): Int
}

@Database(entities = [CardRow::class, OutboxRow::class], version = 1)
abstract class AppDatabase : RoomDatabase() {
    abstract fun cards(): CardDao
    abstract fun outbox(): OutboxDao

    companion object {
        // Version 1 changes freely until the first release APK; migrations start from there
        // (PROJECT_PLAN.md §3). Deliberately no fallbackToDestructiveMigration(): a missing
        // migration must crash, not silently delete unsent swipes.
        fun open(context: Context): AppDatabase =
            Room.databaseBuilder(context, AppDatabase::class.java, "swiperss.db").build()
    }
}
