package io.github.shanirub.swiperss

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.viewModels
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Scaffold
import androidx.compose.ui.Modifier
import androidx.lifecycle.viewmodel.initializer
import androidx.lifecycle.viewmodel.viewModelFactory
import io.github.shanirub.swiperss.swipe.SwipeScreen
import io.github.shanirub.swiperss.swipe.SwipeViewModel
import io.github.shanirub.swiperss.ui.theme.SwipeRSSTheme

class MainActivity : ComponentActivity() {
    // Created on first use and kept by Android across screen rebuilds.
    private val swipeViewModel: SwipeViewModel by viewModels {
        viewModelFactory { initializer { (application as SwipeRssApp).swipeViewModel() } }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            SwipeRSSTheme {
                Scaffold(modifier = Modifier.fillMaxSize()) { innerPadding ->
                    SwipeScreen(swipeViewModel, Modifier.padding(innerPadding))
                }
            }
        }
    }

    // Each time the app becomes visible: on launch and on returning from another app.
    override fun onStart() {
        super.onStart()
        swipeViewModel.onForeground()
    }
}
