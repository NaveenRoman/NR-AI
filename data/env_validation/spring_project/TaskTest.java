package com.nrai.task;

public class TaskTest {
    public static void main(String[] args) {
        TaskEntity entity = new TaskEntity(101, "Real Spring Boot Java Compile");
        if (entity.getId() == 101 && "Real Spring Boot Java Compile".equals(entity.getName())) {
            System.out.println("JAVA_TEST_PASSED: " + entity.getName());
        } else {
            System.exit(1);
        }
    }
}
