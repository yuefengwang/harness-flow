package com.settlement.engine;

import com.settlement.calendar.TradingCalendar;
import com.settlement.model.SettlementInput;
import com.settlement.model.SettlementRule;
import org.junit.jupiter.api.Test;

import java.time.LocalDate;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.*;

class SettlementEngineTest {

    private TradingCalendar mockCalendar(Set<LocalDate> days) {
        return date -> days.contains(date);
    }

    @Test
    void t1FromMondayReturnsPreviousFriday() {
        // 2026-05-25 is Monday, T+1 => previous Friday 2026-05-22
        var tradingDays = Set.of(
            LocalDate.of(2026, 5, 25),  // Mon
            LocalDate.of(2026, 5, 22),  // Fri
            LocalDate.of(2026, 5, 21),  // Thu
            LocalDate.of(2026, 5, 20)   // Wed
        );
        var engine = new SettlementEngine(mockCalendar(tradingDays));
        var input = new SettlementInput(LocalDate.of(2026, 5, 25), SettlementRule.T1);

        LocalDate result = engine.calculate(input);
        assertEquals(LocalDate.of(2026, 5, 22), result);
    }

    @Test
    void t2FromMondayReturnsPreviousThursday() {
        var tradingDays = Set.of(
            LocalDate.of(2026, 5, 25), LocalDate.of(2026, 5, 22),
            LocalDate.of(2026, 5, 21), LocalDate.of(2026, 5, 20)
        );
        var engine = new SettlementEngine(mockCalendar(tradingDays));
        var input = new SettlementInput(LocalDate.of(2026, 5, 25), SettlementRule.T2);

        assertEquals(LocalDate.of(2026, 5, 21), engine.calculate(input));
    }

    @Test
    void t3FromMondayReturnsPreviousWednesday() {
        var tradingDays = Set.of(
            LocalDate.of(2026, 5, 25), LocalDate.of(2026, 5, 22),
            LocalDate.of(2026, 5, 21), LocalDate.of(2026, 5, 20)
        );
        var engine = new SettlementEngine(mockCalendar(tradingDays));
        var input = new SettlementInput(LocalDate.of(2026, 5, 25), SettlementRule.T3);

        assertEquals(LocalDate.of(2026, 5, 20), engine.calculate(input));
    }

    @Test
    void skipsWeekendDays() {
        // 2026-05-25 Monday, T+2 with Sat/Sun not in trading days
        // Should skip Sat 05-23 and Sun 05-24
        var tradingDays = Set.of(
            LocalDate.of(2026, 5, 25),  // Mon (生息日)
            LocalDate.of(2026, 5, 22),  // Fri
            LocalDate.of(2026, 5, 21),  // Thu
            LocalDate.of(2026, 5, 20)   // Wed
        );
        var engine = new SettlementEngine(mockCalendar(tradingDays));
        var input = new SettlementInput(LocalDate.of(2026, 5, 25), SettlementRule.T2);

        assertEquals(LocalDate.of(2026, 5, 21), engine.calculate(input));
    }

    @Test
    void throwsWhenInsufficientTradingDays() {
        // Only 1 trading day before date, but T+3 needs 3
        var tradingDays = Set.of(
            LocalDate.of(2026, 5, 25),
            LocalDate.of(2026, 5, 22)
        );
        var engine = new SettlementEngine(mockCalendar(tradingDays));
        var input = new SettlementInput(LocalDate.of(2026, 5, 25), SettlementRule.T3);

        assertThrows(IllegalArgumentException.class, () -> engine.calculate(input));
    }

    @Test
    void t1FromFridayReturnsThursday() {
        var tradingDays = Set.of(
            LocalDate.of(2026, 5, 22),  // Fri
            LocalDate.of(2026, 5, 21)   // Thu
        );
        var engine = new SettlementEngine(mockCalendar(tradingDays));
        var input = new SettlementInput(LocalDate.of(2026, 5, 22), SettlementRule.T1);

        assertEquals(LocalDate.of(2026, 5, 21), engine.calculate(input));
    }
}
