package io.github.shanirub.swiperss.api

import okhttp3.Interceptor
import okhttp3.Response

// Adds the API token (PROJECT_PLAN.md §3 API auth) to every request of the client it is installed
// in. That client only talks to our server, so the token never goes anywhere else.
class BearerTokenInterceptor(private val token: String) : Interceptor {
    override fun intercept(chain: Interceptor.Chain): Response =
        chain.proceed(
            chain.request().newBuilder().header("Authorization", "Bearer $token").build()
        )
}
