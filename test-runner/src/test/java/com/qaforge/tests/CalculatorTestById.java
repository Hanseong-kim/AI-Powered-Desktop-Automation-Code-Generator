package com.qaforge.tests;

import java.net.MalformedURLException;
import java.net.URL;
import java.time.Duration;
import org.openqa.selenium.By;
import org.openqa.selenium.WebElement;
import org.openqa.selenium.interactions.Actions;
import org.openqa.selenium.support.ui.ExpectedConditions;
import org.openqa.selenium.support.ui.WebDriverWait;
import org.testng.Assert;
import org.testng.annotations.AfterClass;
import org.testng.annotations.BeforeClass;
import org.testng.annotations.Test;
import io.appium.java_client.AppiumBy;
import io.appium.java_client.windows.WindowsDriver;
import io.appium.java_client.windows.options.WindowsOptions;

public class CalculatorTestById {
    private WindowsDriver driver;
    private CalculatorPageById calculatorPage;

    @BeforeClass
    public void setUp() throws Exception {
        // 1. Launch (works for Win32 and UWP; setApp(exePath) alone fails for UWP)
        new ProcessBuilder("C:\\Windows\\System32\\calc.exe").start();

        // 2. Root session — wait for the window, capture its native handle
        WindowsOptions desktopOpts = new WindowsOptions();
        desktopOpts.setApp("Root");
        WindowsDriver desktopDriver = new WindowsDriver(new URL("http://127.0.0.1:4723"), desktopOpts);
        WebDriverWait desktopWait = new WebDriverWait(desktopDriver, Duration.ofSeconds(15));
        WebElement appWindow = desktopWait.until(
                ExpectedConditions.presenceOfElementLocated(
                        By.xpath("//Window[contains(@Name,'계산기')]")));
        String hexHandle = "0x" + Long.toHexString(Long.parseLong(appWindow.getAttribute("NativeWindowHandle")));
        desktopDriver.quit();

        // 3. Attach to the running window via appTopLevelWindow
        WindowsOptions options = new WindowsOptions();
        options.setCapability("appTopLevelWindow", hexHandle);
        driver = new WindowsDriver(new URL("http://127.0.0.1:4723"), options);
        calculatorPage = new CalculatorPageById(driver);
    }

    @Test
    public void testCalculator() {
        System.out.println("[STEP 1] Click on Five button");
        calculatorPage.clickFiveButton();

        System.out.println("[STEP 2] Type 5 in Display field");
        calculatorPage.typeInDisplayField("5");

        System.out.println("[STEP 3] Click on Plus button");
        calculatorPage.clickPlusButton();

        System.out.println("[STEP 4] Click on Three button");
        calculatorPage.clickThreeButton();

        System.out.println("[STEP 5] Type 3 in Display field");
        calculatorPage.typeInDisplayField("3");

        System.out.println("[STEP 6] Click on Equals button");
        calculatorPage.clickEqualsButton();

        System.out.println("[STEP 7] Double click on Result display");
        calculatorPage.doubleClickResultDisplay();

        System.out.println("[STEP 8] Scroll the Calculator window");
        calculatorPage.scrollCalculatorWindow();

        System.out.println("[STEP 9] Right click on Result display");
        calculatorPage.rightClickResultDisplay();

        Assert.assertTrue(calculatorPage.getResultDisplay().isDisplayed());
    }

    @AfterClass
    public void tearDown() {
        driver.quit();
    }

    private class CalculatorPageById {
        private WindowsDriver driver;

        public CalculatorPageById(WindowsDriver driver) {
            this.driver = driver;
        }

        private WebElement getFiveButton() {
            return new WebDriverWait(driver, Duration.ofSeconds(15)).until(
                    ExpectedConditions.elementToBeClickable(AppiumBy.accessibilityId("num5Button")));
        }

        public void clickFiveButton() {
            getFiveButton().click();
        }

        private WebElement getDisplayField() {
            return new WebDriverWait(driver, Duration.ofSeconds(15)).until(
                    ExpectedConditions.presenceOfElementLocated(AppiumBy.accessibilityId("CalculatorResults")));
        }

        public void typeInDisplayField(String value) {
            getDisplayField().clear();
            getDisplayField().sendKeys(value);
        }

        private WebElement getPlusButton() {
            return new WebDriverWait(driver, Duration.ofSeconds(15)).until(
                    ExpectedConditions.elementToBeClickable(AppiumBy.accessibilityId("plusButton")));
        }

        public void clickPlusButton() {
            getPlusButton().click();
        }

        private WebElement getThreeButton() {
            return new WebDriverWait(driver, Duration.ofSeconds(15)).until(
                    ExpectedConditions.elementToBeClickable(AppiumBy.accessibilityId("num3Button")));
        }

        public void clickThreeButton() {
            getThreeButton().click();
        }

        private WebElement getEqualsButton() {
            return new WebDriverWait(driver, Duration.ofSeconds(15)).until(
                    ExpectedConditions.elementToBeClickable(AppiumBy.accessibilityId("equalButton")));
        }

        public void clickEqualsButton() {
            getEqualsButton().click();
        }

        private WebElement getResultDisplay() {
            return new WebDriverWait(driver, Duration.ofSeconds(15)).until(
                    ExpectedConditions.presenceOfElementLocated(AppiumBy.accessibilityId("CalculatorResults")));
        }

        public void doubleClickResultDisplay() {
            Actions actions = new Actions(driver);
            actions.doubleClick(getResultDisplay()).perform();
        }

        public void scrollCalculatorWindow() {
            Actions actions = new Actions(driver);
            actions.moveByOffset(0, -3).perform();
        }

        public void rightClickResultDisplay() {
            Actions actions = new Actions(driver);
            actions.contextClick(getResultDisplay()).perform();
        }
    }
}