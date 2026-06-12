package com.qaforge.tests;

import java.net.MalformedURLException;
import java.net.URL;
import java.time.Duration;

import org.openqa.selenium.By;
import org.openqa.selenium.WebDriver;
import org.openqa.selenium.WebElement;
import org.openqa.selenium.interactions.Actions;
import org.openqa.selenium.support.ui.ExpectedConditions;
import org.openqa.selenium.support.ui.WebDriverWait;
import org.testng.annotations.AfterClass;
import org.testng.annotations.BeforeClass;
import org.testng.annotations.Test;
import org.testng.Assert;

import io.appium.java_client.AppiumBy;
import io.appium.java_client.windows.WindowsDriver;
import io.appium.java_client.windows.options.WindowsOptions;

class CalculatorPageById {
    private WebDriver driver;

    public CalculatorPageById(WebDriver driver) {
        this.driver = driver;
    }

    public void clickFiveButton() {
        WebDriverWait wait = new WebDriverWait(driver, Duration.ofSeconds(15));
        WebElement fiveButton = wait.until(ExpectedConditions.elementToBeClickable(AppiumBy.accessibilityId("num5Button")));
        fiveButton.click();
    }

    public void typeInDisplay(String value) {
        WebDriverWait wait = new WebDriverWait(driver, Duration.ofSeconds(15));
        WebElement display = wait.until(ExpectedConditions.presenceOfElementLocated(AppiumBy.accessibilityId("CalculatorResults")));
        display.clear();
        display.sendKeys(value);
    }

    public void clickPlusButton() {
        WebDriverWait wait = new WebDriverWait(driver, Duration.ofSeconds(15));
        WebElement plusButton = wait.until(ExpectedConditions.elementToBeClickable(AppiumBy.accessibilityId("plusButton")));
        plusButton.click();
    }

    public void clickThreeButton() {
        WebDriverWait wait = new WebDriverWait(driver, Duration.ofSeconds(15));
        WebElement threeButton = wait.until(ExpectedConditions.elementToBeClickable(AppiumBy.accessibilityId("num3Button")));
        threeButton.click();
    }

    public void clickEqualsButton() {
        WebDriverWait wait = new WebDriverWait(driver, Duration.ofSeconds(15));
        WebElement equalsButton = wait.until(ExpectedConditions.elementToBeClickable(AppiumBy.accessibilityId("equalButton")));
        equalsButton.click();
    }

    public void doubleClickResultDisplay() {
        WebDriverWait wait = new WebDriverWait(driver, Duration.ofSeconds(15));
        WebElement resultDisplay = wait.until(ExpectedConditions.elementToBeClickable(AppiumBy.accessibilityId("CalculatorResults")));
        Actions actions = new Actions(driver);
        actions.doubleClick(resultDisplay).perform();
    }

    public void scrollWindow() {
        WebDriverWait wait = new WebDriverWait(driver, Duration.ofSeconds(15));
        WebElement window = wait.until(ExpectedConditions.presenceOfElementLocated(By.className("ApplicationFrameWindow")));
        Actions actions = new Actions(driver);
        actions.moveToElement(window).scrollByAmount(0, -300).perform();
    }
}

public class CalculatorTestById {
    private WebDriver driver;
    private CalculatorPageById calculatorPage;

    @BeforeClass
    public void setup() throws MalformedURLException {
        WindowsOptions options = new WindowsOptions();
        options.setApp("C:\\Windows\\System32\\calc.exe");
        driver = new WindowsDriver(new URL("http://127.0.0.1:4723"), options);
        calculatorPage = new CalculatorPageById(driver);
    }

    @Test
    public void testCalculator() {
        System.out.println("[STEP 1] Click Five button");
        calculatorPage.clickFiveButton();

        System.out.println("[STEP 2] Type 5 in Display");
        calculatorPage.typeInDisplay("5");

        System.out.println("[STEP 3] Click Plus button");
        calculatorPage.clickPlusButton();

        System.out.println("[STEP 4] Click Three button");
        calculatorPage.clickThreeButton();

        System.out.println("[STEP 5] Type 3 in Display");
        calculatorPage.typeInDisplay("3");

        System.out.println("[STEP 6] Click Equals button");
        calculatorPage.clickEqualsButton();

        System.out.println("[STEP 7] Double click Result display");
        calculatorPage.doubleClickResultDisplay();

        System.out.println("[STEP 8] Scroll window");
        calculatorPage.scrollWindow();

        WebDriverWait wait = new WebDriverWait(driver, Duration.ofSeconds(15));
        WebElement resultDisplay = wait.until(ExpectedConditions.presenceOfElementLocated(AppiumBy.accessibilityId("CalculatorResults")));
        Assert.assertTrue(resultDisplay.isDisplayed());
    }

    @AfterClass
    public void tearDown() {
        driver.quit();
    }
}