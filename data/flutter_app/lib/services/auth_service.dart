class AuthService {
  String? _currentUserEmail;

  String? get currentUserEmail => _currentUserEmail;
  bool get isAuthenticated => _currentUserEmail != null;

  Future<bool> signIn(String email, String password) async {
    await Future.delayed(const Duration(milliseconds: 100));
    if (email.contains('@') && password.length >= 6) {
      _currentUserEmail = email;
      return true;
    }
    return false;
  }

  Future<void> signOut() async {
    _currentUserEmail = null;
  }
}
