package com.settlement.calendar;

import java.time.LocalDate;

public interface TradingCalendar {
    boolean isTradingDay(LocalDate date);
}
