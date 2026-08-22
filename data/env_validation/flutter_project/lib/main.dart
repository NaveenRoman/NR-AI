class TaskItem {
  final int id;
  final String title;
  TaskItem({required this.id, required this.title});
  Map<String, dynamic> toJson() => {'id': id, 'title': title};
}

void main() {
  final task = TaskItem(id: 1, title: 'Flutter Real Execution');
  print('Task created: ${task.toJson()}');
}
