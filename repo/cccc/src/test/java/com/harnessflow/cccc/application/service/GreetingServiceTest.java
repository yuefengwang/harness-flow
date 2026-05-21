package com.harnessflow.cccc.application.service;

import com.harnessflow.cccc.domain.model.Greeting;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

class GreetingServiceTest {

    private final GreetingService service = new GreetingService();

    @Test
    void greet_withName_shouldReturnHelloMessage() {
        Greeting result = service.greet("Alice");
        assertEquals("Hello, Alice!", result.message());
    }

    @Test
    void greet_withNullName_shouldDefaultToWorld() {
        Greeting result = service.greet(null);
        assertEquals("Hello, World!", result.message());
    }

    @Test
    void greet_shouldIncludeNonNullTimestamp() {
        Greeting result = service.greet("Test");
        assertNotNull(result.timestamp());
    }

    @Test
    void greet_withEmptyName_shouldReturnHelloEmpty() {
        Greeting result = service.greet("");
        assertEquals("Hello, !", result.message());
    }
}
