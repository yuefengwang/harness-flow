package com.settlement.calendar;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.LocalDate;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.*;

class ChinaExchangeCalendarTest {

    @Test
    void loadsTradingDaysFromCacheFile(@TempDir Path tempDir) throws IOException {
        String json = """
            {
              "year": 2026,
              "trading_days": ["2026-01-05", "2026-01-06", "2026-01-07"]
            }
            """;
        Path cacheFile = tempDir.resolve(".settlement").resolve("calendar-2026.json");
        Files.createDirectories(cacheFile.getParent());
        Files.writeString(cacheFile, json);

        var fetcher = new CalendarFetcher() {
            @Override
            public Set<LocalDate> fetch(int year) {
                fail("should not call fetch when cache exists");
                return Set.of();
            }
        };

        var calendar = new ChinaExchangeCalendar(fetcher, tempDir);
        calendar.load(2026);

        assertTrue(calendar.isTradingDay(LocalDate.of(2026, 1, 5)));
        assertTrue(calendar.isTradingDay(LocalDate.of(2026, 1, 6)));
        assertFalse(calendar.isTradingDay(LocalDate.of(2026, 1, 1)));
    }

    @Test
    void fallsBackToFetcherWhenNoCache(@TempDir Path tempDir) {
        var fetcher = new CalendarFetcher() {
            @Override
            public Set<LocalDate> fetch(int year) {
                return Set.of(
                    LocalDate.of(2026, 1, 5),
                    LocalDate.of(2026, 1, 6)
                );
            }
        };

        var calendar = new ChinaExchangeCalendar(fetcher, tempDir);
        calendar.load(2026);

        assertTrue(calendar.isTradingDay(LocalDate.of(2026, 1, 5)));
        assertFalse(calendar.isTradingDay(LocalDate.of(2026, 1, 4)));
    }

    @Test
    void refreshSkipsCache(@TempDir Path tempDir) throws IOException {
        String old = """
            {"year": 2026, "trading_days": ["2026-01-05"]}
            """;
        Path cacheFile = tempDir.resolve(".settlement").resolve("calendar-2026.json");
        Files.createDirectories(cacheFile.getParent());
        Files.writeString(cacheFile, old);

        var fetcher = new CalendarFetcher() {
            @Override
            public Set<LocalDate> fetch(int year) {
                return Set.of(
                    LocalDate.of(2026, 1, 5),
                    LocalDate.of(2026, 1, 6),
                    LocalDate.of(2026, 1, 7)
                );
            }
        };

        var calendar = new ChinaExchangeCalendar(fetcher, tempDir);
        calendar.refresh(2026);

        assertTrue(calendar.isTradingDay(LocalDate.of(2026, 1, 7)));
    }
}
