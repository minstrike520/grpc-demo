plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("com.google.protobuf")
}

android {
    namespace = "com.example.remotesim"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.example.remotesim"
        minSdk = 26          // VibrationEffect.createOneShot(millis, amplitude) 需要 26+
        targetSdk = 35
        versionCode = 1
        versionName = "0.1.0"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
    buildFeatures { viewBinding = true }

    // protobuf-gradle-plugin 與 AGP 8.7/Kotlin 2.0 的來源集整合缺口：手動註冊生成碼
    // （java.srcDir 同時涵蓋 .kt：kotlin 編譯會掃描 java srcDir）
    // 注意：0.9.6 輸出在 build/generated/sources/（有 s），非舊版 build/generated/source/
    sourceSets {
        getByName("debug") {
            java.srcDir(layout.buildDirectory.dir("generated/sources/proto/debug"))
        }
        getByName("release") {
            java.srcDir(layout.buildDirectory.dir("generated/sources/proto/release"))
        }
    }
}

// 讓 Kotlin/Java 編譯依賴 proto 生成（避免「來源還沒生成就編譯」；configureEach 為懶惰註冊）
tasks.configureEach {
    if (name == "compileDebugKotlin" || name == "compileDebugJavaWithJavac") {
        dependsOn("generateDebugProto")
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.activity:activity-ktx:1.9.3")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.7")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.9.0")

    // gRPC（grpc-kotlin + okhttp，plaintext over Tailscale）
    implementation("io.grpc:grpc-okhttp:1.66.0")
    implementation("io.grpc:grpc-stub:1.66.0")
    implementation("io.grpc:grpc-android:1.66.0")
    implementation("io.grpc:grpc-kotlin-stub:1.4.1")
    implementation("io.grpc:grpc-protobuf-lite:1.66.0")
    implementation("com.google.protobuf:protobuf-javalite:3.25.3")
    implementation("javax.annotation:javax.annotation-api:1.3.2")
}

// protobuf/grpc 程式碼生成：java lite + java grpc + kotlin grpc
// proto 放在 app/src/main/proto/remote_sim.proto
protobuf {
    protoc {
        artifact = "com.google.protobuf:protoc:3.25.3"
    }
    plugins {
        create("grpc") {
            artifact = "io.grpc:protoc-gen-grpc-java:1.66.0"
        }
        create("grpckt") {
            artifact = "io.grpc:protoc-gen-grpc-kotlin:1.4.1:jdk8@jar"
        }
    }
    generateProtoTasks {
        all().forEach {
            it.plugins {
                create("grpc") { option("lite") }
                create("grpckt") { option("lite") }
            }
            it.builtins {
                create("java") { option("lite") }
            }
        }
    }
}