package com.harnessflow.cccc;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.client.TestRestTemplate;
import org.springframework.http.ResponseEntity;

import static org.assertj.core.api.Assertions.assertThat;

@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class CcccApplicationTests {

    @Autowired
    private TestRestTemplate restTemplate;

    @Test
    void contextLoads() {
    }

    @Test
    void greetingEndpoint_shouldReturnHelloMessage() {
        ResponseEntity<String> response = restTemplate.getForEntity(
                "/api/greeting?name=Harness", String.class);
        assertThat(response.getStatusCode().value()).isEqualTo(200);
        assertThat(response.getBody()).contains("\"Hello, Harness!\"");
    }

    @Test
    void greetingEndpoint_withoutName_shouldDefaultToWorld() {
        ResponseEntity<String> response = restTemplate.getForEntity(
                "/api/greeting", String.class);
        assertThat(response.getStatusCode().value()).isEqualTo(200);
        assertThat(response.getBody()).contains("\"Hello, World!\"");
    }
}
