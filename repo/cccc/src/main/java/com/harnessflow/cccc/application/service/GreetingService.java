package com.harnessflow.cccc.application.service;

import com.harnessflow.cccc.application.port.in.GreetingUseCase;
import com.harnessflow.cccc.domain.model.Greeting;

import java.time.Instant;

public class GreetingService implements GreetingUseCase {

    private static final String HELLO_TEMPLATE = "Hello, %s!";

    @Override
    public Greeting greet(String name) {
        String message = HELLO_TEMPLATE.formatted(name != null ? name : "World");
        return new Greeting(message, Instant.now());
    }
}
