package com.settlement.engine;

import com.settlement.calendar.TradingCalendar;
import com.settlement.model.SettlementInput;

import java.time.LocalDate;

public class SettlementEngine {

    private final TradingCalendar calendar;

    public SettlementEngine(TradingCalendar calendar) {
        this.calendar = calendar;
    }

    public LocalDate calculate(SettlementInput input) {
        int remaining = input.rule().days();
        LocalDate cursor = input.date().minusDays(1);
        LocalDate earliest = input.date().minusYears(1);

        while (remaining > 0 && cursor.isAfter(earliest)) {
            if (calendar.isTradingDay(cursor)) {
                remaining--;
            }
            if (remaining == 0) {
                break;
            }
            cursor = cursor.minusDays(1);
        }

        if (remaining > 0) {
            throw new IllegalArgumentException(
                "Cannot calculate purchase date: insufficient trading days before " + input.date());
        }

        return cursor;
    }
}
