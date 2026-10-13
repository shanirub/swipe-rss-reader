package io.github.shanirub.swiperss.db

import android.content.ContextWrapper
import androidx.room.Room
import androidx.room.RoomDatabase
import androidx.sqlite.driver.bundled.BundledSQLiteDriver
import kotlinx.coroutines.Dispatchers

// A real SQLite in memory: the bundled driver brings its own SQLite for the desktop, since
// Android's is missing on the JVM. Room's Android builder wants a Context; for an in-memory
// database it only uses it to pick the journal mode and query thread, both set here, so an
// empty placeholder is enough. (If a Room update starts using it, the tests fail loudly.)
fun inMemoryDatabase(): AppDatabase =
    Room.inMemoryDatabaseBuilder(ContextWrapper(null), AppDatabase::class.java)
        .setDriver(BundledSQLiteDriver())
        .setJournalMode(RoomDatabase.JournalMode.TRUNCATE)
        .setQueryCoroutineContext(Dispatchers.IO)
        .build()
