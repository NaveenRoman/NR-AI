import React, { useState, useEffect } from "react";
import * as api from "./api";
import "./styles.css";

export default function App() {
  const [user, setUser] = useState(null);
  const [token, setToken] = useState(localStorage.getItem("token") || "");
  const [tasks, setTasks] = useState([]);
  const [filter, setFilter] = useState("all");
  const [newTitle, setNewTitle] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [newPriority, setNewPriority] = useState("medium");
  const [authMode, setAuthMode] = useState("login");
  const [usernameInput, setUsernameInput] = useState("");
  const [passwordInput, setPasswordInput] = useState("");
  const [errorMsg, setErrorMsg] = useState("");

  const loadTasks = async () => {
    if (!token) return;
    try {
      const data = await api.getTasks(token, filter === "all" ? "" : filter);
      if (data.tasks) setTasks(data.tasks);
    } catch (e) {
      setErrorMsg("Failed to load tasks.");
    }
  };

  useEffect(() => {
    if (token) loadTasks();
  }, [token, filter]);

  const handleAuth = async (e) => {
    e.preventDefault();
    setErrorMsg("");
    const fn = authMode === "login" ? api.login : api.register;
    const res = await fn(usernameInput, passwordInput);
    if (res.token) {
      setToken(res.token);
      setUser(res.username || usernameInput);
      localStorage.setItem("token", res.token);
    } else {
      setErrorMsg(res.error || "Authentication failed.");
    }
  };

  const handleCreateTask = async (e) => {
    e.preventDefault();
    if (!newTitle.trim()) return;
    const res = await api.createTask(token, {
      title: newTitle,
      description: newDesc,
      priority: newPriority,
      status: "pending",
    });
    if (res.task) {
      setNewTitle("");
      setNewDesc("");
      loadTasks();
    }
  };

  const handleToggleStatus = async (task) => {
    const nextStatus = task.status === "completed" ? "pending" : "completed";
    await api.updateTask(token, task.id, { status: nextStatus });
    loadTasks();
  };

  const handleDeleteTask = async (id) => {
    await api.deleteTask(token, id);
    loadTasks();
  };

  if (!token) {
    return (
      <div className="auth-container">
        <div className="auth-card">
          <h2>Task Manager - {authMode === "login" ? "Sign In" : "Register"}</h2>
          {errorMsg && <div className="error-badge">{errorMsg}</div>}
          <form onSubmit={handleAuth}>
            <input
              type="text"
              placeholder="Username"
              value={usernameInput}
              onChange={(e) => setUsernameInput(e.target.value)}
              required
            />
            <input
              type="password"
              placeholder="Password"
              value={passwordInput}
              onChange={(e) => setPasswordInput(e.target.value)}
              required
            />
            <button type="submit">{authMode === "login" ? "Login" : "Sign Up"}</button>
          </form>
          <p onClick={() => setAuthMode(authMode === "login" ? "register" : "login")}>
            {authMode === "login" ? "Need an account? Register" : "Have an account? Sign in"}
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="app-container">
      <header className="app-header">
        <h1>Task Manager</h1>
        <button className="logout-btn" onClick={() => { setToken(""); localStorage.removeItem("token"); }}>Logout</button>
      </header>
      <main className="main-content">
        <form className="task-form" onSubmit={handleCreateTask}>
          <input
            type="text"
            placeholder="New task title..."
            value={newTitle}
            onChange={(e) => setNewTitle(e.target.value)}
            required
          />
          <input
            type="text"
            placeholder="Description (optional)"
            value={newDesc}
            onChange={(e) => setNewDesc(e.target.value)}
          />
          <select value={newPriority} onChange={(e) => setNewPriority(e.target.value)}>
            <option value="low">Low Priority</option>
            <option value="medium">Medium Priority</option>
            <option value="high">High Priority</option>
          </select>
          <button type="submit">Add Task</button>
        </form>
        <div className="filter-bar">
          {["all", "pending", "in_progress", "completed"].map((f) => (
            <button
              key={f}
              className={filter === f ? "active-filter" : ""}
              onClick={() => setFilter(f)}
            >
              {f.toUpperCase()}
            </button>
          ))}
        </div>
        <div className="task-grid">
          {tasks.map((task) => (
            <div key={task.id} className={`task-card ${task.status}`}>
              <div className="task-card-header">
                <h3>{task.title}</h3>
                <span className={`badge ${task.priority}`}>{task.priority}</span>
              </div>
              <p>{task.description}</p>
              <div className="task-card-actions">
                <button onClick={() => handleToggleStatus(task)}>
                  {task.status === "completed" ? "Mark Pending" : "Mark Complete"}
                </button>
                <button className="delete-btn" onClick={() => handleDeleteTask(task.id)}>Delete</button>
              </div>
            </div>
          ))}
        </div>
      </main>
    </div>
  );
}
