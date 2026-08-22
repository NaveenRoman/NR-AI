import 'package:flutter/material.dart';
import '../models/task_model.dart';
import '../services/api_service.dart';

class HomeScreen extends StatefulWidget {
  final String title;

  const HomeScreen({super.key, required this.title});

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  final ApiService _api = ApiService();
  List<TaskModel> _tasks = [];
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _loadTasks();
  }

  Future<void> _loadTasks() async {
    final tasks = await _api.fetchTasks();
    setState(() {
      _tasks = tasks;
      _loading = false;
    });
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: Text(widget.title)),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : ListView.builder(
              itemCount: _tasks.length,
              itemBuilder: (context, index) {
                final task = _tasks[index];
                return ListTile(
                  title: Text(task.title),
                  subtitle: Text('Status: ${task.status}'),
                  leading: Icon(
                    task.status == 'completed'
                        ? Icons.check_circle
                        : Icons.pending_actions,
                    color: task.status == 'completed'
                        ? Colors.green
                        : Colors.orange,
                  ),
                );
              },
            ),
      floatingActionButton: FloatingActionButton(
        onPressed: () {
          setState(() {
            _tasks.add(TaskModel(
              id: _tasks.length + 1,
              title: 'New Task ${_tasks.length + 1}',
            ));
          });
        },
        child: const Icon(Icons.add),
      ),
    );
  }
}
