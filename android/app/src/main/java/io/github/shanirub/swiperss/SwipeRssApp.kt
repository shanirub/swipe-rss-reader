package io.github.shanirub.swiperss

import android.app.Application
import android.os.SystemClock
import io.github.shanirub.swiperss.api.ApiClient
import io.github.shanirub.swiperss.db.AppDatabase
import io.github.shanirub.swiperss.swipe.SwipeRepository
import io.github.shanirub.swiperss.swipe.SwipeViewModel

// One object per app process (declared in AndroidManifest.xml), so the database and the HTTP client
// exist once, however often Android rebuilds a screen. Hand-made instead of Hilt (PROJECT_PLAN.md
// §3 Android: no dependency injection framework for the MVP).
class SwipeRssApp : Application() {
    val database by lazy { AppDatabase.open(this) }
    val api by lazy { ApiClient.create(BuildConfig.BASE_URL, BuildConfig.API_TOKEN) }

    fun swipeViewModel() = SwipeViewModel(
        SwipeRepository(api, database, appVersion = BuildConfig.VERSION_CODE),
        monotonicMs = SystemClock::elapsedRealtime,
    )
}
