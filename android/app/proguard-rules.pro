# gRPC/protobuf 相關 keep 規則（release 建置用）
-keep class io.grpc.** { *; }
-keep class remotesim.** { *; }
-dontwarn io.grpc.**
-dontwarn com.google.protobuf.**
-dontwarn javax.annotation.**