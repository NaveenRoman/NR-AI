import 'package:test/test.dart';
import 'package:flutter_app/models/task_model.dart';

void main() {
  group('TaskModel Tests', () {
    test('JSON Serialization works correctly', () {
      const task = TaskModel(id: 1, title: 'Test Task', status: 'completed');
      final json = task.toJson();
      expect(json['id'], equals(1));
      expect(json['title'], equals('Test Task'));
      expect(json['status'], equals('completed'));
    });

    test('JSON Deserialization works correctly', () {
      final json = {'id': 2, 'title': 'Parsed Task', 'status': 'pending'};
      final task = TaskModel.fromJson(json);
      expect(task.id, equals(2));
      expect(task.title, equals('Parsed Task'));
      expect(task.status, equals('pending'));
    });
  });
}
