package com.settlement.calendar;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.LocalDate;
import java.util.Collections;
import java.util.HashSet;
import java.util.Set;

public class CalendarFetcher {

    private static final String BASE_URL =
        "https://raw.githubusercontent.com/icebeam7/chinese-trading-calendar/main/json/";

    private final HttpClient httpClient;
    private final ObjectMapper mapper;

    public CalendarFetcher() {
        this.httpClient = HttpClient.newHttpClient();
        this.mapper = new ObjectMapper();
    }

    public Set<LocalDate> fetch(int year) throws IOException, InterruptedException {
        String url = BASE_URL + year + ".json";
        HttpRequest request = HttpRequest.newBuilder()
                .uri(URI.create(url))
                .GET()
                .build();
        HttpResponse<String> response = httpClient.send(request,
                HttpResponse.BodyHandlers.ofString());

        if (response.statusCode() != 200) {
            throw new IOException("HTTP " + response.statusCode() + ": " + url);
        }

        JsonNode root = mapper.readTree(response.body());
        JsonNode daysNode = root.get("trading_days");
        if (daysNode == null || !daysNode.isArray()) {
            return Collections.emptySet();
        }

        Set<LocalDate> tradingDays = new HashSet<>();
        for (JsonNode day : daysNode) {
            tradingDays.add(LocalDate.parse(day.asText()));
        }
        return Collections.unmodifiableSet(tradingDays);
    }
}
