package com.harnessflow.cccc.infrastructure.config;

import com.harnessflow.cccc.application.port.in.GreetingUseCase;
import com.harnessflow.cccc.application.service.GreetingService;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class GreetingBeanConfig {

    @Bean
    public GreetingUseCase greetingUseCase() {
        return new GreetingService();
    }
}
