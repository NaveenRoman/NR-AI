class TaskModel {
  final int id;
  final String title;
  final String status;
  final String priority;

  const TaskModel({
    required this.id,
    required this.title,
    this.status = 'pending',
    this.priority = 'medium',
  });

  factory TaskModel.fromJson(Map<String, dynamic> json) {
    return TaskModel(
      id: json['id'] as int,
      title: json['title'] as String,
      status: json['status'] as String? ?? 'pending',
      priority: json['priority'] as String? ?? 'medium',
    );
  }

  Map<String, dynamic> toJson() {
    return {
      'id': id,
      'title': title,
      'status': status,
      'priority': priority,
    };
  }
}
