package io.github.shanirub.swiperss.swipe

import android.content.ActivityNotFoundException
import android.widget.Toast
import androidx.browser.customtabs.CustomTabsIntent
import androidx.compose.animation.core.VectorConverter
import androidx.compose.animation.core.animate
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.scale
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.input.pointer.util.VelocityTracker
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import androidx.core.net.toUri
import io.github.shanirub.swiperss.api.Action
import io.github.shanirub.swiperss.api.Card
import kotlinx.coroutines.launch
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.format.DateTimeParseException
import kotlin.math.roundToInt

private val FLING_SPEED = 1000.dp  // per second

@Composable
fun SwipeScreen(viewModel: SwipeViewModel, modifier: Modifier = Modifier) {
    val state by viewModel.state.collectAsState()
    val context = LocalContext.current

    Column(modifier.fillMaxSize().padding(16.dp)) {
        state.problem?.let {
            Text(it, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall)
        }
        val cards = state.cards
        Box(Modifier.weight(1f).fillMaxWidth(), contentAlignment = Alignment.Center) {
            when {
                cards == null -> {}  // Room hasn't answered yet: a few milliseconds
                cards.isEmpty() && state.loading -> CircularProgressIndicator()
                cards.isEmpty() -> Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text("No new headlines")
                    Spacer(Modifier.height(8.dp))
                    Button(onClick = viewModel::retry) { Text("Try again") }
                }
                else -> {
                    // The next card waits underneath, so a swipe reveals it.
                    cards.getOrNull(1)?.let { next ->
                        key(next.feedId, next.itemKey) {
                            CardContent(next, pending = null, Modifier.fillMaxSize().scale(0.95f))
                        }
                    }
                    val top = cards.first()
                    // key(): a new card gets fresh drag state instead of inheriting the old offset.
                    key(top.feedId, top.itemKey) {
                        SwipeableCard(top, onSwiped = { action ->
                            viewModel.swipe(top, action)
                            if (action == Action.READ_NOW) readableLink(top)?.let { link ->
                                try {
                                    CustomTabsIntent.Builder().build().launchUrl(context, link.toUri())
                                } catch (e: ActivityNotFoundException) {
                                    Toast.makeText(context, "No browser to open the link", Toast.LENGTH_LONG).show()
                                }
                            }
                        })
                    }
                }
            }
        }
        val canReadNow = cards?.firstOrNull()?.let { readableLink(it) != null } ?: false
        Row(Modifier.fillMaxWidth().padding(top = 8.dp), horizontalArrangement = Arrangement.SpaceBetween) {
            Text("← never")
            Text(
                "↑ read now",
                color = if (canReadNow) MaterialTheme.colorScheme.onSurface
                else MaterialTheme.colorScheme.onSurface.copy(alpha = 0.3f),
            )
            Text("save →")
        }
    }
}

// The top card follows the finger; when let go it either flies off (a swipe) or springs back.
@Composable
private fun SwipeableCard(card: Card, onSwiped: (Action) -> Unit) {
    var drag by remember { mutableStateOf(Offset.Zero) }
    var gone by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    val canReadNow = readableLink(card) != null
    val flingSpeed = with(LocalDensity.current) { FLING_SPEED.toPx() }

    BoxWithConstraints(Modifier.fillMaxSize()) {
        val width = constraints.maxWidth.toFloat()
        val height = constraints.maxHeight.toFloat()
        fun decide(vx: Float = 0f, vy: Float = 0f) =
            swipeAction(drag.x, drag.y, vx, vy, width / 3, height / 3, flingSpeed, canReadNow)

        CardContent(
            card,
            pending = decide(),
            Modifier
                .fillMaxSize()
                // The gesture is read on the box, which stays put; the card inside moves. (Reading
                // it on the moving card would make every finger position look the same.)
                .pointerInput(card, width, height) {
                    val tracker = VelocityTracker()
                    detectDragGestures(
                        onDragStart = { tracker.resetTracking() },
                        onDrag = { change, amount ->
                            if (!gone) {
                                tracker.addPosition(change.uptimeMillis, change.position)
                                drag += amount
                            }
                        },
                        onDragEnd = {
                            if (gone) return@detectDragGestures
                            val v = tracker.calculateVelocity()
                            val action = decide(v.x, v.y)
                            if (action != null) gone = true
                            val target = when (action) {
                                null -> Offset.Zero
                                Action.SAVE -> Offset(width * 1.5f, drag.y)
                                Action.NEVER -> Offset(-width * 1.5f, drag.y)
                                Action.READ_NOW -> Offset(drag.x, -height * 1.5f)
                            }
                            scope.launch {
                                animate(Offset.VectorConverter, drag, target) { value, _ -> drag = value }
                                action?.let(onSwiped)
                            }
                        },
                        onDragCancel = {
                            if (!gone) scope.launch {
                                animate(Offset.VectorConverter, drag, Offset.Zero) { value, _ -> drag = value }
                            }
                        },
                    )
                },
            cardModifier = Modifier
                .offset { IntOffset(drag.x.roundToInt(), drag.y.roundToInt()) }
                .graphicsLayer { rotationZ = drag.x / width * 15f },
        )
    }
}

@Composable
private fun CardContent(
    card: Card,
    pending: Action?,
    modifier: Modifier = Modifier,
    cardModifier: Modifier = Modifier,
) {
    Box(modifier) {
        Card(cardModifier.fillMaxSize()) {
            Box(Modifier.fillMaxSize().padding(20.dp)) {
                Column {
                    Text(
                        "${card.feedId} · ${shownDate(card)}",
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Spacer(Modifier.height(12.dp))
                    Text(card.headline, style = MaterialTheme.typography.headlineSmall)
                    card.author?.let {
                        Spacer(Modifier.height(4.dp))
                        Text(it, style = MaterialTheme.typography.labelLarge)
                    }
                    Spacer(Modifier.height(12.dp))
                    Text(card.summary, style = MaterialTheme.typography.bodyLarge, overflow = TextOverflow.Ellipsis)
                }
                // What letting go now would do.
                pending?.let {
                    Text(
                        when (it) {
                            Action.NEVER -> "NEVER"
                            Action.SAVE -> "SAVE"
                            Action.READ_NOW -> "READ NOW"
                        },
                        Modifier.align(Alignment.BottomCenter),
                        style = MaterialTheme.typography.headlineMedium,
                        color = MaterialTheme.colorScheme.primary,
                    )
                }
            }
        }
    }
}

private val dateFormat = DateTimeFormatter.ofPattern("d MMM, HH:mm")

// The publication date in local time; the fetch time if the feed gave none.
private fun shownDate(card: Card): String {
    val raw = card.publishedAt ?: card.fetchedAt
    return try {
        OffsetDateTime.parse(raw).atZoneSameInstant(ZoneId.systemDefault()).format(dateFormat)
    } catch (e: DateTimeParseException) {
        raw
    }
}
