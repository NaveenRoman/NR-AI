import 'package:flutter/material.dart';
import 'theme/app_theme.dart';
import 'screens/home_screen.dart';

void main() {
  runApp(const NRFlutterApp());
}

class NRFlutterApp extends StatelessWidget {
  const NRFlutterApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'NR AI Flutter App',
      theme: AppTheme.lightTheme,
      home: const HomeScreen(title: 'NR AI Flutter App'),
      debugShowCheckedModeBanner: false,
    );
  }
}
