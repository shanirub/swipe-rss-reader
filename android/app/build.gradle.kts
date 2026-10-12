import java.util.Properties

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.kotlin.serialization)
    alias(libs.plugins.ksp)
    alias(libs.plugins.room)
}

// API token and server address (PROJECT_PLAN.md §3) come from android/secrets.properties, which git
// ignores; the template is secrets.properties.example. Like the server without its token, the build
// fails if a value is missing or invalid. Error messages never contain the values.
val secretsFile = rootProject.layout.projectDirectory.file("secrets.properties")
val secrets = Properties().apply {
    providers.fileContents(secretsFile).asText.orNull?.let { load(it.reader()) }
}

fun secret(key: String, check: (String) -> Boolean, rule: String): String {
    val value = secrets.getProperty(key)?.trim().orEmpty()
    if (value.isEmpty() || !check(value)) {
        throw GradleException(
            "$key in ${secretsFile.asFile} is missing or invalid ($rule). " +
                "Template: android/secrets.properties.example"
        )
    }
    return value
}

// A Kotlin string literal for buildConfigField.
fun quoted(value: String) = "\"" + value.replace("\\", "\\\\").replace("\"", "\\\"") + "\""

android {
    namespace = "io.github.shanirub.swiperss"
    compileSdk {
        version = release(37)
    }

    defaultConfig {
        applicationId = "io.github.shanirub.swiperss"
        minSdk = 36
        targetSdk = 37
        versionCode = 1
        versionName = "1.0"

        buildConfigField(
            "String", "API_TOKEN",
            quoted(secret("swiperss.apiToken", { it.length >= 32 }, "at least 32 characters, as on the server")),
        )
        buildConfigField(
            "String", "BASE_URL",
            quoted(secret("swiperss.baseUrl", { it.startsWith("https://") }, "must start with https://")),
        )
    }

    buildTypes {
        release {
            optimization {
                enable = true
                packageScope = setOf("androidx.**", "kotlin.**", "kotlinx.**")
            }
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_11
        targetCompatibility = JavaVersion.VERSION_11
    }
    buildFeatures {
        compose = true
        buildConfig = true  // off by default in recent AGP; holds API_TOKEN and BASE_URL
    }
}

room {
    schemaDirectory("$projectDir/schemas")
}

dependencies {
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.compose.material3)
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.ui.graphics)
    implementation(libs.androidx.compose.ui.tooling.preview)
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.lifecycle.runtime.ktx)
    implementation(libs.kotlinx.serialization.json)
    implementation(libs.retrofit)
    implementation(libs.retrofit.converter.kotlinx.serialization)
    implementation(libs.room.runtime)
    ksp(libs.room.compiler)
    implementation(libs.okhttp)  // explicit: Retrofit 3.0.0 asks for OkHttp 4.12
    testImplementation(libs.junit)
    testImplementation(libs.okhttp.mockwebserver)
    testImplementation(libs.kotlinx.coroutines.test)
    testImplementation(libs.sqlite.bundled.jvm)
    testImplementation(libs.json.schema.validator)  // conformance test only, never in the APK
    debugImplementation(libs.androidx.compose.ui.tooling)
}
// SpecConformanceTest reads the API spec. Declared as an input, so a spec change re-runs the tests
// instead of Gradle reusing the last result.
val openapiSpec = rootProject.layout.projectDirectory.file("../api/openapi.yaml")
tasks.withType<Test>().configureEach {
    inputs.file(openapiSpec).withPathSensitivity(PathSensitivity.NONE)
    systemProperty("swiperss.openapiSpec", openapiSpec.asFile.absolutePath)
}
