package io.github.shanirub.swiperss.swipe

import io.github.shanirub.swiperss.api.SwipeRssApi
import io.github.shanirub.swiperss.db.OutboxDao
import io.github.shanirub.swiperss.db.OutboxRow
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import retrofit2.HttpException

// Sends the outbox to POST /swipes (PROJECT_PLAN.md §3 swipe recording, invalid swipes). Swipes go
// as stored, byte for byte. A swipe leaves the outbox only after the server accepted it (200); a
// resend is harmless, the server skips a swipe_id it already has.
class SwipeSync(
    private val api: SwipeRssApi,
    private val outbox: OutboxDao,
    private val appVersion: Int,
) {
    // Sends everything pending, in batches. Throws when sending has to stop (IOException: no
    // connection; HttpException other than 422: server error or bad token); what wasn't sent stays
    // in the outbox for the next try.
    suspend fun sendPending() {
        while (true) {
            val batch = outbox.pending(BATCH_LIMIT)
            if (batch.isEmpty()) return
            try {
                post(batch)
                outbox.delete(batch.map { it.swipeId })
            } catch (e: HttpException) {
                if (e.code() != 422) throw e
                sendOneByOne(batch)
            }
        }
    }

    // The server rejects a whole batch if one swipe is invalid. Alone, each valid swipe gets in;
    // one that is rejected alone becomes a dead letter, so it never blocks the others.
    private suspend fun sendOneByOne(batch: List<OutboxRow>) {
        for (row in batch) {
            try {
                post(listOf(row))
                outbox.delete(listOf(row.swipeId))
            } catch (e: HttpException) {
                if (e.code() != 422) throw e
                outbox.markDead(row.swipeId, errorText(e), appVersion)
            }
        }
    }

    private suspend fun post(rows: List<OutboxRow>) {
        val json = rows.joinToString(",", prefix = """{"swipes":[""", postfix = "]}") { it.swipeJson }
        api.postSwipesJson(json.toRequestBody(JSON))
    }

    // The server's validation errors, for the dead letter (and the server logs them too).
    private fun errorText(e: HttpException): String {
        val body = try {
            e.response()?.errorBody()?.string().orEmpty()
        } catch (_: java.io.IOException) {
            ""
        }
        return "HTTP ${e.code()}: ${body.take(ERROR_MAX_CHARS)}"
    }

    companion object {
        const val BATCH_LIMIT = 500  // the spec's maximum per request
        private const val ERROR_MAX_CHARS = 2000
        private val JSON = "application/json".toMediaType()
    }
}
