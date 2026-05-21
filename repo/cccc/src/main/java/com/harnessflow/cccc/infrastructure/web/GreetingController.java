package com.harnessflow.cccc.infrastructure.web;

import com.harnessflow.cccc.application.port.in.GreetingUseCase;
import com.harnessflow.cccc.domain.model.Greeting;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api")
public class GreetingController {

    private final GreetingUseCase greetingUseCase;

    public GreetingController(GreetingUseCase greetingUseCase) {
        this.greetingUseCase = greetingUseCase;
    }

    @GetMapping("/greeting")
    public Greeting greet(@RequestParam(name = "name", defaultValue = "World") String name) {
        return greetingUseCase.greet(name);
    }
}
