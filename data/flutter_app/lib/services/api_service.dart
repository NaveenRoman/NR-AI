import 'dart:convert';
import '../models/task_model.dart';

class ApiService {
  final String baseUrl;

  ApiService({this.baseUrl = 'http://127.0.0.1:8088'});

  Future<List<TaskModel>> fetchTasks() async {
    // Mock / fallback async fetch
    await Future.delayed(const Duration(milliseconds: 50));
    return [
      const TaskModel(id: 1, title: 'Explore NR AI Architecture', status: 'completed'),
      const TaskModel(id: 2, title: 'Build Flutter Application', status: 'in_progress'),
    ];
  }

  Future<TaskModel> createTask(String title) async {
    await Future.delayed(const Duration(milliseconds: 50));
    return TaskModel(id: DateTime.now().millisecondsSinceEpoch, title: title);
  }
}
