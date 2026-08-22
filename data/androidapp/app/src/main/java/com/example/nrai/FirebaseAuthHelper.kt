package com.example.nrai

class FirebaseAuthHelper {
    fun signInWithEmail(email: String, pass: String): Boolean {
        return email.isNotBlank() && pass.length >= 6
    }
    fun signOut() {}
}
