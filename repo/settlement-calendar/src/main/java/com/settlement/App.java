package com.settlement;

import com.settlement.calendar.ChinaExchangeCalendar;
import com.settlement.calendar.TradingCalendar;
import com.settlement.engine.SettlementEngine;
import com.settlement.model.SettlementInput;

import java.time.LocalDate;
import java.time.format.DateTimeParseException;

public class App {

    public static void main(String[] args) {
        System.exit(run(args));
    }

    static int run(String[] args) {
        String dateStr = null;
        String ruleStr = null;
        boolean refresh = false;

        for (int i = 0; i < args.length; i++) {
            switch (args[i]) {
                case "--date":
                case "-d":
                    if (i + 1 < args.length) dateStr = args[++i];
                    break;
                case "--rule":
                case "-r":
                    if (i + 1 < args.length) ruleStr = args[++i];
                    break;
                case "--refresh":
                    refresh = true;
                    break;
                case "--help":
                case "-h":
                    printHelp();
                    return 0;
                default:
                    System.err.println("未知参数: " + args[i]);
                    printHelp();
                    return 1;
            }
        }

        if (dateStr == null || ruleStr == null) {
            System.err.println("错误: 缺少必要参数 --date 和 --rule");
            printHelp();
            return 1;
        }

        LocalDate date;
        SettlementInput input;
        try {
            input = SettlementInput.parse(dateStr, ruleStr);
            date = input.date();
        } catch (DateTimeParseException e) {
            System.err.println("错误: 日期格式无效，应为 yyyy-MM-dd");
            return 1;
        } catch (IllegalArgumentException e) {
            System.err.println("错误: 规则无效，可选 T1/T2/T3，收到: " + ruleStr);
            return 1;
        }

        TradingCalendar calendar = new ChinaExchangeCalendar();
        int year = date.getYear();
        try {
            if (refresh) {
                ((ChinaExchangeCalendar) calendar).refresh(year);
            } else {
                ((ChinaExchangeCalendar) calendar).load(year);
            }
        } catch (RuntimeException e) {
            if (e.getCause() instanceof java.io.IOException) {
                System.err.println("错误: 无法获取交易日历，请检查网络后重试");
                return 3;
            }
            System.err.println("错误: 加载交易日历失败 - " + e.getMessage());
            return 2;
        }

        if (!calendar.isTradingDay(date)) {
            String dayOfWeek = date.getDayOfWeek().toString();
            System.err.println("警告: 输入日期 " + date + " (" + dayName(dayOfWeek) + ") 为非交易日，推算结果可能不准确");
        }

        SettlementEngine engine = new SettlementEngine(calendar);
        LocalDate purchaseDate;
        try {
            purchaseDate = engine.calculate(input);
        } catch (IllegalArgumentException e) {
            System.err.println("错误: " + input.rule().days() + " 个交易日");
            return 4;
        }

        System.out.println("输入日期(生息日): " + date + " (" + dayName(date.getDayOfWeek().toString()) + ")");
        System.out.println("结算规则: T+" + input.rule().days());
        System.out.println("推算买入日期: " + purchaseDate + " (" + dayName(purchaseDate.getDayOfWeek().toString()) + ")");

        return 0;
    }

    private static String dayName(String day) {
        return switch (day) {
            case "MONDAY" -> "周一";
            case "TUESDAY" -> "周二";
            case "WEDNESDAY" -> "周三";
            case "THURSDAY" -> "周四";
            case "FRIDAY" -> "周五";
            case "SATURDAY" -> "周六";
            case "SUNDAY" -> "周日";
            default -> day;
        };
    }

    private static void printHelp() {
        System.out.println("""
            结算日期倒推工具

            用法: java -jar settlement-calendar.jar --date <日期> --rule <规则> [--refresh]

            参数:
              --date, -d    生息日期 (yyyy-MM-dd)
              --rule, -r    结算规则 (T1 / T2 / T3)
              --refresh     强制刷新交易日历缓存
              --help, -h    显示帮助

            示例:
              java -jar settlement-calendar.jar --date 2026-05-25 --rule T1
              java -jar settlement-calendar.jar -d 2026-05-25 -r T2 --refresh

            日历缓存路径: ~/.settlement/calendar-{year}.json
            """);
    }
}
