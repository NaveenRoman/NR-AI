package com.example.nrai.controller;

import com.example.nrai.model.Account;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import java.util.*;

@RestController
@RequestMapping("/api/accounts")
@CrossOrigin(origins = "*")
public class AccountController {
    private final List<Account> accounts = new ArrayList<>();

    @GetMapping
    public List<Account> getAllAccounts() {
        if (accounts.isEmpty()) {
            accounts.add(new Account("ACC-1001", 5000.0, "John Doe"));
            accounts.add(new Account("ACC-1002", 12500.5, "Jane Smith"));
        }
        return accounts;
    }

    @PostMapping
    public ResponseEntity<Account> createAccount(@RequestBody Account account) {
        accounts.add(account);
        return ResponseEntity.ok(account);
    }
}
