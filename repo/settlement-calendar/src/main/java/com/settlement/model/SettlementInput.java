package com.settlement.model;

import java.time.LocalDate;

public record SettlementInput(LocalDate date, SettlementRule rule) {

    public static SettlementInput parse(String dateStr, String ruleStr) {
        LocalDate date = LocalDate.parse(dateStr);
        SettlementRule rule = SettlementRule.fromString(ruleStr);
        return new SettlementInput(date, rule);
    }
}
