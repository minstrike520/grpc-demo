// Root build：只宣告 plugin 版本，不 apply
plugins {
    id("com.android.application") version "8.7.3" apply false
    id("org.jetbrains.kotlin.android") version "2.0.21" apply false
    id("com.google.protobuf") version "0.9.6" apply false
}