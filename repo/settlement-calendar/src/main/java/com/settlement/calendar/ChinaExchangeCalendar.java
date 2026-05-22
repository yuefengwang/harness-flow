package com.settlement.calendar;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.LocalDate;
import java.util.HashSet;
import java.util.Set;

public class ChinaExchangeCalendar implements TradingCalendar {

    private static final String CACHE_DIR_NAME = ".settlement";

    private final CalendarFetcher fetcher;
    private final Path cacheDir;
    private final ObjectMapper mapper;
    private final Set<LocalDate> tradingDays = new HashSet<>();

    public ChinaExchangeCalendar() {
        this(new CalendarFetcher(), Path.of(System.getProperty("user.home")));
    }

    ChinaExchangeCalendar(CalendarFetcher fetcher, Path homeDir) {
        this.fetcher = fetcher;
        this.cacheDir = homeDir.resolve(CACHE_DIR_NAME);
        this.mapper = new ObjectMapper();
    }

    public void load(int year) {
        Path cacheFile = cacheDir.resolve("calendar-" + year + ".json");
        try {
            if (Files.exists(cacheFile)) {
                loadFromCache(cacheFile);
            } else {
                fetchAndCache(year);
            }
        } catch (IOException | InterruptedException e) {
            throw new RuntimeException("Failed to load calendar for year " + year, e);
        }
    }

    public void refresh(int year) {
        try {
            fetchAndCache(year);
        } catch (IOException | InterruptedException e) {
            throw new RuntimeException("Failed to refresh calendar for year " + year, e);
        }
    }

    private void loadFromCache(Path cacheFile) throws IOException {
        String json = Files.readString(cacheFile);
        parseTradingDays(json);
    }

    private void fetchAndCache(int year) throws IOException, InterruptedException {
        Set<LocalDate> fetched = fetcher.fetch(year);
        tradingDays.clear();
        tradingDays.addAll(fetched);

        Files.createDirectories(cacheDir);
        Path cacheFile = cacheDir.resolve("calendar-" + year + ".json");
        ObjectNode root = mapper.createObjectNode();
        root.put("year", year);
        ArrayNode daysArray = root.putArray("trading_days");
        fetched.stream().map(LocalDate::toString).forEach(daysArray::add);
        String json = mapper.writerWithDefaultPrettyPrinter().writeValueAsString(root);
        Files.writeString(cacheFile, json);
    }

    private void parseTradingDays(String json) throws IOException {
        tradingDays.clear();
        JsonNode root = mapper.readTree(json);
        JsonNode days = root.get("trading_days");
        if (days != null && days.isArray()) {
            for (JsonNode day : days) {
                tradingDays.add(LocalDate.parse(day.asText()));
            }
        }
    }

    @Override
    public boolean isTradingDay(LocalDate date) {
        return tradingDays.contains(date);
    }
}
