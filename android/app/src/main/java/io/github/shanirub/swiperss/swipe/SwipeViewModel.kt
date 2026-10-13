package io.github.shanirub.swiperss.swipe

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import io.github.shanirub.swiperss.api.Action
import io.github.shanirub.swiperss.api.Card
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.onEach
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import kotlinx.serialization.SerializationException
import retrofit2.HttpException
import java.io.IOException

data class SwipeUiState(
    val cards: List<Card>?,  // null until Room has answered once
    val loading: Boolean,
    val problem: String?,  // why the last refill failed; null once one succeeds
)

// Holds the swipe screen's state and logic. Android keeps a ViewModel alive while it rebuilds the
// screen (rotation, dark mode switch), so a running refill and the card timer survive that.
class SwipeViewModel(
    private val repository: SwipeRepository,
    monotonicMs: () -> Long,
) : ViewModel() {
    private val timer = CardTimer(monotonicMs)
    private val loading = MutableStateFlow(false)
    private val problem = MutableStateFlow<String?>(null)
    private var refillJob: Job? = null

    val state: StateFlow<SwipeUiState> =
        combine(repository.cards.onEach { timer.show(it.firstOrNull()) }, loading, problem) { cards, l, p ->
            SwipeUiState(cards, l, p)
        }.stateIn(viewModelScope, SharingStarted.Eagerly, SwipeUiState(null, loading = false, problem = null))

    // The app came to the foreground (also after a rotation, which restarts the timer: rare enough
    // not to matter).
    fun onForeground() {
        timer.restart()
        refill()
    }

    fun retry() = refill()

    fun swipe(card: Card, action: Action) {
        val timeToSwipeMs = timer.elapsedFor(card)
        viewModelScope.launch {
            repository.swipe(card, action, timeToSwipeMs)
            refill()
        }
    }

    // One refill at a time. A failure keeps the stack as it is; the next swipe, the retry button
    // or the next return to the app tries again.
    private fun refill() {
        if (refillJob?.isActive == true) return
        refillJob = viewModelScope.launch {
            loading.value = true
            problem.value = try {
                repository.refill()
                null
            } catch (e: IOException) {
                "Offline: can't reach the server (is Tailscale on?)"
            } catch (e: HttpException) {
                "Server error ${e.code()}"
            } catch (e: SerializationException) {
                "Unreadable answer from the server"
            } finally {
                loading.value = false
            }
        }
    }
}
