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
  private CalculatorPageById calculatorPageById;

  @BeforeClass
  public void setUp() throws Exception {
    new ProcessBuilder("C:\\Windows\\System32\\calc.exe").start();

    WindowsOptions desktopOpts = new WindowsOptions();
    desktopOpts.setApp("Root");
    WindowsDriver desktopDriver = new WindowsDriver(new URL("http://127.0.0.1:4723"), desktopOpts);
    WebDriverWait desktopWait = new WebDriverWait(desktopDriver, Duration.ofSeconds(15));
    WebElement appWindow = desktopWait.until(
        ExpectedConditions.presenceOfElementLocated(
            By.xpath("//Window[contains(@Name,'Calculator')]")));
    String hexHandle = "0x" + Long.toHexString(Long.parseLong(appWindow.getAttribute("NativeWindowHandle")));
    desktopDriver.quit();

    WindowsOptions options = new WindowsOptions();
    options.setCapability("appTopLevelWindow", hexHandle);
    driver = new WindowsDriver(new URL("http://127.0.0.1:4723"), options);
    calculatorPageById = new CalculatorPageById(driver);
  }

  @Test
  public void testCalculator() {
    System.out.println("[STEP 1] Click on Five button");
    calculatorPageById.clickFiveButton();

    System.out.println("[STEP 2] Type 5 in Display field");
    calculatorPageById.typeInDisplayField("5");

    System.out.println("[STEP 3] Click on Plus button");
    calculatorPageById.clickPlusButton();

    System.out.println("[STEP 4] Click on Three button");
    calculatorPageById.clickThreeButton();

    System.out.println("[STEP 5] Type 3 in Display field");
    calculatorPageById.typeInDisplayField("3");

    System.out.println("[STEP 6] Click on Equals button");
    calculatorPageById.clickEqualsButton();

    System.out.println("[STEP 7] Double click on Result display");
    calculatorPageById.doubleClickOnResultDisplay();

    System.out.println("[STEP 8] Scroll the window");
    calculatorPageById.scrollWindow();

    System.out.println("[STEP 9] Right click on Result display");
    calculatorPageById.rightClickOnResultDisplay();

    Assert.assertTrue(calculatorPageById.isResultDisplayDisplayed());
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

    public void clickFiveButton() {
      WebDriverWait wait = new WebDriverWait(driver, Duration.ofSeconds(15));
      WebElement fiveButton = wait.until(ExpectedConditions.elementToBeClickable(AppiumBy.accessibilityId("num5Button")));
      fiveButton.click();
    }

    public void typeInDisplayField(String value) {
      WebDriverWait wait = new WebDriverWait(driver, Duration.ofSeconds(15));
      WebElement displayField = wait.until(ExpectedConditions.presenceOfElementLocated(AppiumBy.accessibilityId("CalculatorResults")));
      displayField.clear();
      displayField.sendKeys(value);
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

    public void doubleClickOnResultDisplay() {
      WebDriverWait wait = new WebDriverWait(driver, Duration.ofSeconds(15));
      WebElement resultDisplay = wait.until(ExpectedConditions.presenceOfElementLocated(AppiumBy.accessibilityId("CalculatorResults")));
      Actions actions = new Actions(driver);
      actions.doubleClick(resultDisplay).perform();
    }

    public void scrollWindow() {
      WebDriverWait wait = new WebDriverWait(driver, Duration.ofSeconds(15));
      WebElement window = wait.until(ExpectedConditions.presenceOfElementLocated(By.className("ApplicationFrameWindow")));
      Actions actions = new Actions(driver);
      actions.moveByOffset(0, -3).perform();
    }

    public void rightClickOnResultDisplay() {
      WebDriverWait wait = new WebDriverWait(driver, Duration.ofSeconds(15));
      WebElement resultDisplay = wait.until(ExpectedConditions.presenceOfElementLocated(AppiumBy.accessibilityId("CalculatorResults")));
      Actions actions = new Actions(driver);
      actions.contextClick(resultDisplay).perform();
    }

    public boolean isResultDisplayDisplayed() {
      WebDriverWait wait = new WebDriverWait(driver, Duration.ofSeconds(15));
      return wait.until(ExpectedConditions.presenceOfElementLocated(AppiumBy.accessibilityId("CalculatorResults"))).isDisplayed();
    }
  }
}