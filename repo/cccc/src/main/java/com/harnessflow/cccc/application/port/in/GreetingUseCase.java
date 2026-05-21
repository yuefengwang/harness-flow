package com.harnessflow.cccc.application.port.in;

import com.harnessflow.cccc.domain.model.Greeting;

public interface GreetingUseCase {
    Greeting greet(String name);
}
