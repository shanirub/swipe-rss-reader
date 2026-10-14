package io.github.shanirub.swiperss.swipe

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import io.github.shanirub.swiperss.api.Action
import io.github.shanirub.swiperss.api.Card
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.onEach
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.serialization.SerializationException
import retrofit2.HttpException
import java.io.IOException

data class SwipeUiState(
    val cards: List<Card>?,  // null until Room has answered once
    val loading: Boolean,
    val problem: String?,  // why the last refill failed; null once one succeeds
    val unsent: Int?,  // swipes waiting to be sent, while sending fails; otherwise null
    val dead: Int,  // swipes the server rejected (dead letters)
)

private data class NetworkStatus(val loading: Boolean = false, val problem: String? = null, val syncFailed: Boolean = false)

// Holds the swipe screen's state and logic. Android keeps a ViewModel alive while it rebuilds the
// screen (rotation, dark mode switch), so a running refill and the card timer survive that.
class SwipeViewModel(
    private val repository: SwipeRepository,
    monotonicMs: () -> Long,
    private val syncDelayMs: Long = 5_000,
) : ViewModel() {
    private val timer = CardTimer(monotonicMs)
    private val status = MutableStateFlow(NetworkStatus())
    private var refillJob: Job? = null
    private var syncTimer: Job? = null
    private var olderDeadLettersRevived = false

    val state: StateFlow<SwipeUiState> =
        combine(
            repository.cards.onEach { timer.show(it.firstOrNull()) },
            status,
            repository.pendingCount,
            repository.deadCount,
        ) { cards, s, pending, dead ->
            SwipeUiState(cards, s.loading, s.problem, pending.takeIf { s.syncFailed && it > 0 }, dead)
        }.stateIn(viewModelScope, SharingStarted.Eagerly, SwipeUiState(null, false, null, null, 0))

    // The app came to the foreground (also after a rotation, which restarts the timer: rare enough
    // not to matter). Pending swipes go first, so the refill sees the server's latest state.
    fun onForeground() {
        timer.restart()
        viewModelScope.launch { syncThenRefill() }
    }

    fun retry() = refill()

    fun retryDeadLetters() {
        viewModelScope.launch {
            repository.reviveAllDeadLetters()
            sync()
        }
    }

    fun swipe(card: Card, action: Action) {
        val timeToSwipeMs = timer.elapsedFor(card)
        viewModelScope.launch {
            repository.swipe(card, action, timeToSwipeMs)
            refill()
        }
        syncSoon()
    }

    // Debounce: the swipes go a few seconds after the last one, together. Each swipe restarts the
    // wait. The sync itself is launched separately, so a later swipe can't cancel a request that is
    // already on its way.
    private fun syncSoon() {
        syncTimer?.cancel()
        syncTimer = viewModelScope.launch {
            delay(syncDelayMs)
            viewModelScope.launch { syncThenRefill() }
        }
    }

    // The refill after a sync matters when the last refill found only cards whose swipes were still
    // waiting (e.g. many swipes piled up while the connection was down): once the server has the
    // swipes, /queue returns new cards.
    private suspend fun syncThenRefill() {
        sync()
        refill()
    }

    // A failure leaves the swipes in the outbox; the next swipe or return to the app tries again.
    private suspend fun sync() {
        val failed = try {
            if (!olderDeadLettersRevived) {
                repository.reviveOlderDeadLetters()
                olderDeadLettersRevived = true
            }
            repository.sync()
            false
        } catch (e: IOException) {
            true
        } catch (e: HttpException) {
            true
        } catch (e: SerializationException) {
            true
        }
        status.update { it.copy(syncFailed = failed) }
    }

    // One refill at a time. A failure keeps the stack as it is; the next swipe, the retry button
    // or the next return to the app tries again.
    private fun refill() {
        if (refillJob?.isActive == true) return
        refillJob = viewModelScope.launch {
            status.update { it.copy(loading = true) }
            val problem = try {
                repository.refill()
                null
            } catch (e: IOException) {
                "Offline: can't reach the server (is Tailscale on?)"
            } catch (e: HttpException) {
                "Server error ${e.code()}"
            } catch (e: SerializationException) {
                "Unreadable answer from the server"
            }
            status.update { it.copy(loading = false, problem = problem) }
        }
    }
}
