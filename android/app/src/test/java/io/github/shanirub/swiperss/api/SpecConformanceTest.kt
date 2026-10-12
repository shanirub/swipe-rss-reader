package io.github.shanirub.swiperss.api

import com.networknt.schema.InputFormat
import com.networknt.schema.Schema
import com.networknt.schema.SchemaLocation
import com.networknt.schema.SchemaRegistry
import com.networknt.schema.dialect.Dialects
import java.io.File
import kotlinx.serialization.KSerializer
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

// The hand-written models in ApiModels.kt against api/openapi.yaml (Key Question 11 h): JSON the
// app sends must be valid for the spec's request schema, and every response the spec allows must
// decode, with null in every nullable field and with values in all of them. Gradle passes the
// spec's path (app/build.gradle.kts), so a spec change re-runs this test.
class SpecConformanceTest {
    private companion object {
        val spec = File(
            System.getProperty("swiperss.openapiSpec") ?: error("system property swiperss.openapiSpec not set")
        )
        // The library doesn't read files on its own; it gets the text under the file's URI.
        val specUri = spec.toURI().toString()
        val registry: SchemaRegistry = SchemaRegistry.withDialect(Dialects.getOpenApi31()) {
            it.schemas(mapOf(specUri to spec.readText()))
        }

        fun schema(name: String): Schema =
            registry.getSchema(SchemaLocation.of("$specUri#/components/schemas/$name"))
    }

    // The spec's validation errors for `json`; `format` (date-time, uuid) is checked too, which
    // JSON Schema 2020-12 leaves off by default.
    private fun errors(schemaName: String, json: String): List<String> =
        schema(schemaName)
            .validate(json, InputFormat.JSON) { context ->
                context.executionConfig { it.formatAssertionsEnabled(true) }
            }
            .map { it.toString() }

    private fun assertValid(schemaName: String, json: String) =
        assertEquals("$schemaName: $json", emptyList<String>(), errors(schemaName, json))

    // A spec-valid response decodes, and encoding it again gives the same JSON: every field
    // arrived under its spec name, none was dropped.
    private fun <T> assertDecodes(serializer: KSerializer<T>, schemaName: String, json: String) {
        assertValid(schemaName, json)  // the sample itself follows the spec
        val decoded = apiJson.decodeFromString(serializer, json)
        assertEquals(
            Json.parseToJsonElement(json),
            Json.parseToJsonElement(apiJson.encodeToString(serializer, decoded)),
        )
    }

    // The spec, not the samples, decides which fields may be null: each field of `element` is set
    // to null on its own, and wherever the spec accepts that, the app must decode it. So a field
    // the spec makes nullable later is covered without editing the samples.
    private fun <T> assertDecodesEachNullableField(
        serializer: KSerializer<T>, schemaName: String, listKey: String, element: String,
    ) {
        val fields = Json.parseToJsonElement(element).jsonObject
        for (key in fields.keys) {
            val json = """{"$listKey": [${JsonObject(fields + (key to JsonNull))}]}"""
            if (errors(schemaName, json).isEmpty()) assertDecodes(serializer, schemaName, json)
        }
    }

    private val itemKey = "guid:" + "0123456789abcdef".repeat(4)

    private val nullCard = """
        {"feed_id": "eff", "item_key": "$itemKey", "headline": "Headline", "summary": "",
         "link": null, "published_at": null, "fetched_at": "2026-10-11T12:00:00Z",
         "author": null, "tags": []}
    """

    private val fullCard = """
        {"feed_id": "the-verge", "item_key": "link:${"fedcba9876543210".repeat(4)}",
         "headline": "Headline", "summary": "Summary", "link": "https://example.org/a",
         "published_at": "2026-10-11T11:00:00+03:00", "fetched_at": "2026-10-11T12:00:00Z",
         "author": "Author", "tags": ["one", "two"]}
    """

    private val nullFeed = """
        {"feed_id": "eff", "name": null, "url": "https://www.eff.org/rss",
         "last_attempt_at": null, "last_success_at": null, "last_new_item_at": null,
         "last_error": null, "consecutive_failures": 0}
    """

    private val fullFeed = """
        {"feed_id": "the-verge", "name": "The Verge", "url": "https://www.theverge.com/rss",
         "last_attempt_at": "2026-10-11T12:15:00Z", "last_success_at": "2026-10-11T12:00:00Z",
         "last_new_item_at": "2026-10-11T11:45:00Z", "last_error": "HTTP 503",
         "consecutive_failures": 2}
    """

    @Test
    fun swipeBatchTheAppSendsIsValid() {
        val cards = listOf(nullCard, fullCard).map { apiJson.decodeFromString(Card.serializer(), it) }
        val swipes = Action.entries.mapIndexed { i, action ->
            Swipe(
                swipeId = "3f1c8a52-7d0e-4b8e-9a51-2c6f0e9d4b1$i",
                action = action,
                swipedAt = "2026-10-11T14:30:00+03:00",
                tzOffsetMinutes = 180,
                timeToSwipeMs = if (i == 0) null else 1500L,
                appVersion = 1,
                card = cards[i % cards.size],
            )
        }

        assertValid("SwipeBatch", apiJson.encodeToString(SwipeBatch.serializer(), SwipeBatch(swipes)))
    }

    @Test
    fun queueResponsesDecode() {
        assertDecodes(QueueResponse.serializer(), "QueueResponse", """{"items": []}""")
        assertDecodes(QueueResponse.serializer(), "QueueResponse", """{"items": [$nullCard, $fullCard]}""")
        assertDecodesEachNullableField(QueueResponse.serializer(), "QueueResponse", "items", fullCard)
    }

    @Test
    fun swipeBatchResultDecodes() {
        assertDecodes(SwipeBatchResult.serializer(), "SwipeBatchResult", """{"stored": 2, "duplicates": 1}""")
    }

    @Test
    fun feedListResponsesDecode() {
        assertDecodes(FeedListResponse.serializer(), "FeedListResponse", """{"feeds": []}""")
        assertDecodes(FeedListResponse.serializer(), "FeedListResponse", """{"feeds": [$nullFeed, $fullFeed]}""")
        assertDecodesEachNullableField(FeedListResponse.serializer(), "FeedListResponse", "feeds", fullFeed)
    }

    // The checks above are only as good as the validator: it must actually load the spec's schemas
    // (an unresolved schema accepts everything) and check formats.
    @Test
    fun validatorRejectsInvalidSwipes() {
        val swipe = """
            {"swipe_id": "3f1c8a52-7d0e-4b8e-9a51-2c6f0e9d4b17", "action": "save",
             "swiped_at": "2026-10-11T14:30:00+03:00", "tz_offset_minutes": 180,
             "time_to_swipe_ms": null, "app_version": 1, "card": $nullCard}
        """
        assertValid("SwipeBatch", """{"swipes": [$swipe]}""")

        val invalid = mapOf(
            "unknown field" to swipe.replace("\"app_version\"", "\"extra\": 1, \"app_version\""),
            "missing field" to swipe.replace("\"time_to_swipe_ms\": null,", ""),
            "unknown action" to swipe.replace("\"save\"", "\"later\""),
            "bad date-time" to swipe.replace("2026-10-11T14:30:00+03:00", "yesterday"),
            "bad uuid" to swipe.replace("3f1c8a52-7d0e", "not-a-uuid"),
            "offset out of range" to swipe.replace("180", "900"),
            "card field renamed" to swipe.replace("\"headline\"", "\"title\""),
        )
        for ((case, json) in invalid) {
            assertTrue("$case: accepted", errors("SwipeBatch", """{"swipes": [$json]}""").isNotEmpty())
        }
    }
}
