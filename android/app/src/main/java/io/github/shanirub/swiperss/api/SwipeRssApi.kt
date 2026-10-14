package io.github.shanirub.swiperss.api

import okhttp3.RequestBody
import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.POST
import retrofit2.http.Query

// The server's endpoints that stage 5 calls (api/openapi.yaml); Retrofit generates the HTTP code.
// A non-2xx answer throws retrofit2.HttpException, whose code() tells 401, 422, 5xx apart; a
// network failure throws an IOException.
interface SwipeRssApi {
    @GET("queue")
    suspend fun queue(@Query("limit") limit: Int): QueueResponse

    @POST("swipes")
    suspend fun postSwipes(@Body batch: SwipeBatch): SwipeBatchResult

    // The same endpoint with a body built elsewhere: sync sends the swipes' stored JSON unchanged.
    @POST("swipes")
    suspend fun postSwipesJson(@Body batch: RequestBody): SwipeBatchResult

    @GET("feeds")
    suspend fun feeds(): FeedListResponse
}
