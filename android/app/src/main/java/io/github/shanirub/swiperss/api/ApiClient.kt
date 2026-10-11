package io.github.shanirub.swiperss.api

import kotlinx.serialization.json.Json
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import retrofit2.Retrofit
import retrofit2.converter.kotlinx.serialization.asConverterFactory
import retrofit2.create

// JSON settings for the API (PROJECT_PLAN.md §3 Android).
val apiJson = Json {
    // Required-but-nullable fields must always be sent: kotlinx.serialization would otherwise omit
    // fields equal to their default value, and the server answers a missing field with 422.
    // The models have no defaults; this keeps it true if one is ever added.
    encodeDefaults = true
    explicitNulls = true  // the default, spelled out: null fields are sent as null, not left out
    // Responses may gain fields (that's why lists are wrapped in objects); older apps ignore them.
    ignoreUnknownKeys = true
}

object ApiClient {
    // baseUrl and token come from BuildConfig in the app; tests pass a MockWebServer address.
    fun create(baseUrl: String, token: String, http: OkHttpClient = OkHttpClient()): SwipeRssApi =
        Retrofit.Builder()
            .baseUrl(baseUrl.toHttpUrl())
            .client(http.newBuilder().addInterceptor(BearerTokenInterceptor(token)).build())
            .addConverterFactory(apiJson.asConverterFactory("application/json".toMediaType()))
            .build()
            .create()
}
